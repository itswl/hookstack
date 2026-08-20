#!/usr/bin/env python3
"""deploy/shadow.yaml is a config the pipe can actually boot, and still a shadow.

`docker compose config` validates deploy/docker-compose.shadow.yml. Nothing
validated the file it mounts. That file is the newest thing in the repository
and the only thing standing between the platform's real production traffic and
two judges, and hookrelay refuses to start on a bad config BY DESIGN — unknown
adapter, unknown processor, a route naming a channel that does not exist. Good
design, wrong discovery moment: without this check the first reader of the file
was a shadow deployment, on live traffic, at whatever hour it went out.

So: load it the way the pipe loads it (Config.from_file — the same call
hookrelay's own gate makes against config.example.yaml), then assert the three
properties the file's own header promises and no schema can express.

    python3 scripts/assert_shadow_config.py [path]          # needs hookrelay's deps
    hookrelay/.venv/bin/python scripts/assert_shadow_config.py deploy/shadow.yaml

scripts/stack-smoke.sh runs it inside the relay container instead, where those
deps are already installed — see the step there.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

# hookrelay is a sibling package here, not an installed one: the repo keeps it
# at <root>/hookrelay/hookrelay and the relay image keeps it at /app/hookrelay.
# Offering both lets one file run from a repo checkout and from inside the
# container the smoke has just started, with no PYTHONPATH plumbing at either
# call site.
sys.path[:0] = [str(Path(__file__).resolve().parent.parent / "hookrelay"), str(Path.cwd())]

import yaml  # noqa: E402

from hookrelay.config import Config, ConfigError  # noqa: E402

ENV_REF = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")
DEFAULT = Path(__file__).resolve().parent.parent / "deploy" / "shadow.yaml"


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else DEFAULT
    # Caught, not raised: a checker that answers a malformed file with a
    # traceback makes the reader debug the checker instead of reading the one
    # line that says which line of YAML is wrong.
    try:
        raw_text = path.read_text(encoding="utf-8")
        raw = yaml.safe_load(raw_text) or {}
    except OSError as err:
        print(f"  FAIL  {path}: unreadable — {err}")
        return 1
    except yaml.YAMLError as err:
        print(f"  FAIL  {path}: not valid YAML — {err}")
        return 1
    if not isinstance(raw, dict):
        print(f"  FAIL  {path}: the top level is {type(raw).__name__}, not a mapping of sources/channels/routes")
        return 1
    problems: list[str] = []

    # Placeholders for every ${NAME} the file mentions, discovered from the file
    # rather than listed here — a list would be one more thing to keep in step
    # with .env, and the point is to prove the REFERENCES resolve, not to know
    # the names. setdefault, so a real exported value still wins.
    for name in sorted(set(re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", raw_text))):
        os.environ.setdefault(name, f"placeholder-{name}")

    # Secrets: a ${NAME} reference, never a literal, on every door and every
    # hop. hookrelay treats an unresolved ref as the empty string and an empty
    # secret as "unsigned source" — so one typo'd variable name turns the door
    # the platform forwards production traffic to into an open one, quietly, at
    # boot, with nothing in the log to say so. Two failure modes, one check.
    # An explicit empty secret is allowed on the hops that never leave the
    # compose network, and only on those: a return door the pipe hands itself,
    # and the bridge it hands a card to. Everything else must carry a ${NAME},
    # and a MISSING secret key is still a failure everywhere — the difference
    # between `secret: ""` and no line at all is the difference between a
    # decision and an oversight.
    internal_hops = {"judge-notify", "to-me"}
    for kind in ("sources", "channels"):
        for item in raw.get(kind) or []:
            name = item.get("name")
            secret = item.get("secret")
            if secret is None:
                problems.append(f"{kind[:-1]} {name!r}: no secret key at all — an unsigned hop by omission")
            elif str(secret).strip() == "":
                if name not in internal_hops:
                    problems.append(
                        f"{kind[:-1]} {name!r}: secret is empty — only an in-network hop may be unsigned, "
                        f"and this one is not on the list"
                    )
            elif not ENV_REF.match(str(secret).strip()):
                problems.append(
                    f"{kind[:-1]} {name!r}: secret is not a ${{NAME}} reference "
                    f"— secrets do not belong in a committed file"
                )

    try:
        cfg = Config.from_file(str(path))
    except (ConfigError, KeyError, TypeError, ValueError) as err:
        print(f"  FAIL  {path}: the pipe would refuse to start — {err}")
        for problem in problems:
            print(f"  FAIL  {problem}")
        return 1

    # The platform's door faces outward and must be signed. The return door does
    # not: it is one container of this deployment handing a verdict to another on
    # a private network, and requiring a secret there would only mean inventing
    # one to satisfy this check. Named explicitly rather than inferred, so adding
    # a THIRD unsigned door stays a decision somebody makes on purpose.
    internal_doors = {"judge-notify"}
    for name, src in cfg.sources.items():
        if name in internal_doors:
            continue
        if not src.secret:
            problems.append(f"source {name!r}: secret resolved empty — the door would accept unsigned events")

    # The rule this file used to enforce was "no chat channel types at all", on
    # the reasoning that a shadow which can page somebody is not a shadow. That
    # was right until 2026-08-20, when the shadow gained the one thing it was
    # missing: somewhere for a human to RULE on a verdict. The ledger could say
    # the two brains agreed 84% of the time and never which one was right, and a
    # ruling has to be asked for somewhere.
    #
    # So the line moved from "which type" to "how far". A chat channel is allowed
    # — it is how a card with buttons reaches one person — but every channel must
    # still resolve on the compose network. A hostname with a dot in it is the
    # thing actually worth refusing: pasting a real bot URL in "just to see the
    # cards once" is how this file stops being a shadow and becomes an
    # unaccountable second notifier for somebody else's alerts.
    for name, ch in cfg.channels.items():
        host = urlparse(ch.url).hostname or ""
        if "." in host or host in ("localhost", "127.0.0.1"):
            problems.append(f"channel {name!r}: {ch.url} leaves the compose network — the verdict goes nowhere else")

    if cfg.escalation is not None:
        problems.append(
            "escalation is configured — the investigator already serves the platform's own deep-analysis "
            "leg, so a second door onto the same runs doubles the model bill for no new information"
        )

    # Both brains, every event: the fan-out IS the experiment. A judge added to
    # the compose file and to `channels` but left out of the route gets nothing,
    # its ledger stays empty, and the three-way comparison has a hole in it that
    # looks exactly like agreement.
    #
    # Checked against the BRAINS specifically, not against every channel. The
    # earlier version demanded that every source reach every channel, which was
    # true while the only channels were the two judges and became wrong the
    # moment a return door and a card channel existed: the platform's door feeds
    # the brains, the return door feeds the card, and neither should feed the
    # other. What must not happen is a brain nobody routes to.
    brains = sorted(name for name in cfg.channels if name.startswith("to-judge") and "feedback" not in name)
    inbound = sorted(name for name in cfg.sources if name not in internal_doors)
    for source in inbound:
        reached = {
            channel
            for route in cfg.routes
            if route.source in (source, "*") and not route.when
            for channel in route.send_to
        }
        missing = [brain for brain in brains if brain not in reached]
        if missing:
            problems.append(
                f"source {source!r} does not reach {', '.join(missing)} unconditionally — "
                f"a brain that is configured but not routed to is an empty ledger, not a comparison"
            )
    if len(brains) < 2:
        problems.append(
            f"only {len(brains)} brain channel(s) found ({', '.join(brains) or 'none'}) — "
            f"a shadow run with one brain is not comparing anything"
        )

    for problem in problems:
        print(f"  FAIL  {problem}")
    if problems:
        print(f"\n{len(problems)} shadow config assertion(s) failed")
        return 1
    # "no way to page anyone" was true until the shadow gained a card somebody
    # can rule on. Saying it now would be this file telling the same kind of lie
    # it exists to catch, so it reports what it actually checked: every hop stays
    # on the compose network, and the brains are both fed.
    chat_channels = sorted(name for name, ch in cfg.channels.items() if ch.type != "generic")
    reach = f"{len(chat_channels)} reach a person ({', '.join(chat_channels)})" if chat_channels else "none reach a person"
    print(
        f"shadow config: {path.name} boots — {len(cfg.sources)} door(s), "
        f"{len(cfg.channels)} channel(s) all in-network, both brains fed, {reach}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
