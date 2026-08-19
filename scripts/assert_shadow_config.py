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
    for kind in ("sources", "channels"):
        for item in raw.get(kind) or []:
            secret = item.get("secret")
            if secret is None:
                problems.append(f"{kind[:-1]} {item.get('name')!r}: no secret — an unsigned hop in a shadow")
            elif not ENV_REF.match(str(secret).strip()):
                problems.append(
                    f"{kind[:-1]} {item.get('name')!r}: secret is not a ${{NAME}} reference "
                    f"— secrets do not belong in a committed file"
                )

    try:
        cfg = Config.from_file(str(path))
    except (ConfigError, KeyError, TypeError, ValueError) as err:
        print(f"  FAIL  {path}: the pipe would refuse to start — {err}")
        for problem in problems:
            print(f"  FAIL  {problem}")
        return 1

    for name, src in cfg.sources.items():
        if not src.secret:
            problems.append(f"source {name!r}: secret resolved empty — the door would accept unsigned events")

    # A shadow that can page somebody is not a shadow. `generic` is the plain
    # webhook type; the chat types (feishu, dingtalk, wecom) exist to reach
    # humans. And a hostname with a dot in it is not on the compose network —
    # that is what pasting a real bot URL in "just to see the cards once" looks
    # like from here, which is the way this file stops being a shadow.
    for name, ch in cfg.channels.items():
        if ch.type != "generic":
            problems.append(f"channel {name!r}: type {ch.type!r} can reach a person — a shadow may not")
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
    for source in cfg.sources:
        reached = {
            channel
            for route in cfg.routes
            if route.source in (source, "*") and not route.when
            for channel in route.send_to
        }
        missing = sorted(set(cfg.channels) - reached)
        if missing:
            problems.append(
                f"source {source!r} does not reach {', '.join(missing)} unconditionally — "
                f"a brain that is configured but not routed to is an empty ledger, not a comparison"
            )

    for problem in problems:
        print(f"  FAIL  {problem}")
    if problems:
        print(f"\n{len(problems)} shadow config assertion(s) failed")
        return 1
    print(
        f"shadow config: {path.name} boots — {len(cfg.sources)} signed door(s), "
        f"{len(cfg.channels)} in-network channel(s), all reached, no way to page anyone"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
