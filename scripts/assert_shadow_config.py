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
COMPOSE = Path(__file__).resolve().parent.parent / "deploy" / "docker-compose.shadow.yml"
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
COMPOSE_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::([-?])([^}]*))?\}")


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _resolve_compose_value(expr: str, env: dict[str, str]) -> str:
    """Resolve one ${VAR}, ${VAR:-default} or ${VAR:?msg} against an env file."""
    match = COMPOSE_VAR.fullmatch(expr.strip())
    if not match:
        return expr.strip()
    name, kind, default = match.group(1), match.group(2), match.group(3) or ""
    value = env.get(name, "")
    if value:
        return value
    if kind == "-":
        # Defaults nest: ${A:-${B:-c}}
        return _resolve_compose_value(default, env) if default.startswith("${") else default
    # ${VAR:?msg} with nothing set — compose refuses to start, so it is not a model.
    return ""


def brain_models(compose_path: Path, env_path: Path) -> dict[str, tuple[str, str]]:
    """service name -> (model it will actually run, where that value came from)."""
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    env = _read_env_file(env_path)
    models: dict[str, tuple[str, str]] = {}
    for name, service in (compose.get("services") or {}).items():
        if not name.startswith("hookjudge"):
            continue
        expr = str((service.get("environment") or {}).get("HOOKJUDGE_AI_MODEL", ""))
        models[name] = (_resolve_compose_value(expr, env), _origin(expr, env))
    return models


def _origin(expr: str, env: dict[str, str]) -> str:
    """Where the value came from: the env file, a compose default, or nowhere."""
    match = COMPOSE_VAR.fullmatch(expr.strip())
    if not match:
        return "literal"
    name, kind = match.group(1), match.group(2)
    if env.get(name):
        return "env"
    return "compose-default" if kind == "-" else "unset"


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
    # A MISSING secret key is a failure everywhere, for sources and channels
    # alike: the difference between `secret: ""` and no line at all is the
    # difference between a decision and an oversight, and only the first one
    # can be reviewed.
    #
    # An explicit empty secret is judged by DIRECTION, because the two ends do
    # not mean the same thing (narrowed 2026-09-03, when the first channel
    # leaving this network arrived):
    #
    #   SOURCE — an inbound door. Empty means anyone who can reach it can
    #     inject events, which is the failure this check was written for: one
    #     typo'd variable name turns the door the platform forwards production
    #     traffic to into an open one, quietly, at boot. Allowed only on the
    #     hops that never leave the compose network, and only those.
    #
    #   CHANNEL — an outbound hop. Empty means we do not authenticate OURSELVES
    #     to the receiver. Nothing is opened by it, and some receivers offer no
    #     signing at all, so requiring it here would forbid destinations rather
    #     than protect anything. It stays a declared decision, not a silent one.
    #
    # For a Feishu custom bot specifically the URL is the whole credential, so
    # signing is a second factor worth turning on where the receiver supports
    # it — recommended, not enforced.
    internal_hops = {"judge-notify", "probe-notify", "to-me"}
    for kind in ("sources", "channels"):
        for item in raw.get(kind) or []:
            name = item.get("name")
            secret = item.get("secret")
            if secret is None:
                problems.append(f"{kind[:-1]} {name!r}: no secret key at all — an unsigned hop by omission")
            elif str(secret).strip() == "":
                if kind == "sources" and name not in internal_hops:
                    problems.append(
                        f"{kind[:-1]} {name!r}: secret is empty — only an in-network inbound door may be unsigned, "
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

    # The platform's door faces outward and must be signed. The two return doors
    # do not: each is one container of this deployment handing its result to
    # another on a private network, and requiring a secret there would only mean
    # inventing one to satisfy this check. Named explicitly rather than inferred,
    # so adding another unsigned door stays a decision somebody makes on purpose.
    internal_doors = {"judge-notify", "probe-notify"}
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
    #
    # Narrowed a second time (2026-09-03) for the same reason, by a door that
    # deliberately feeds NO brain: the attention watcher arrives pre-judged and
    # the brains are calibrated on alerts, so routing it to them would spend
    # three model calls to re-judge a colleague's question in severity
    # vocabulary. "Every source reaches every brain" would have forbidden that
    # bypass; the sentence above never asked for it. Two invariants say what
    # was actually meant:
    #
    #   1. no PARTIAL fan-out — a source feeding one brain feeds all of them,
    #      or the comparison has a hole for that source that reads as agreement;
    #   2. no STARVED brain — every brain is fed by at least one source.
    #
    # A source feeding zero brains is now legal, and has to be, or a pipe can
    # only ever carry things worth judging.
    brains = sorted(name for name in cfg.channels if name.startswith("to-judge") and "feedback" not in name)
    inbound = sorted(name for name in cfg.sources if name not in internal_doors)
    fed_brains: set[str] = set()
    for source in inbound:
        reached = {
            channel
            for route in cfg.routes
            if route.source in (source, "*") and not route.when
            for channel in route.send_to
        }
        hit = [brain for brain in brains if brain in reached]
        fed_brains.update(hit)
        if not hit:
            continue  # a deliberate bypass; invariant 2 below still guards the brains
        missing = [brain for brain in brains if brain not in reached]
        if missing:
            problems.append(
                f"source {source!r} reaches {', '.join(hit)} but not {', '.join(missing)} — "
                f"a partial fan-out leaves that source's comparison a hole shaped like agreement"
            )
    starved = [brain for brain in brains if brain not in fed_brains]
    if starved:
        problems.append(
            f"brain channel(s) no source routes to: {', '.join(starved)} — "
            f"a brain that is configured but not routed to is an empty ledger, not a comparison"
        )
    # The failure this catches has happened twice and was invisible both times:
    # every brain pointed at ONE model, so the shadow spent 386 samples (Aug) and
    # then a whole morning (Sep) comparing a model with itself while every
    # container stayed healthy and every ledger kept filling. Nothing asserted
    # the sides were configured to DIFFER, so nothing complained.
    #
    # Only checkable where the env file is: inside the smoke's container compose
    # has already resolved these away. Skipping is fine; skipping SILENTLY is not,
    # so the summary says which of the two happened.
    model_check = "brain models unchecked (no .env beside the compose file)"
    if COMPOSE.is_file() and ENV_FILE.is_file():
        resolved = brain_models(COMPOSE, ENV_FILE)

        # 1. The flattening. Twice now, every brain has pointed at ONE model while
        #    every container stayed healthy and every ledger kept filling: 386
        #    samples in August, a morning in September. Nothing asserted the sides
        #    were configured to DIFFER, so nothing complained.
        seen: dict[str, list[str]] = {}
        for name, (model, _) in resolved.items():
            if model:
                seen.setdefault(model, []).append(name)
        for model, names in sorted((m, n) for m, n in seen.items() if len(n) > 1):
            problems.append(
                f"{' and '.join(sorted(names))} both run {model} — a shadow comparing a model "
                f"with itself measures its own noise floor (83% importance, measured Aug 2026), not two brains"
            )

        # 2. The stale vendor default. A/B fall back to hard-coded DeepSeek names
        #    when their env vars are missing, so a half-finished provider swap
        #    silently points a brain at a vendor nobody chose.
        defaulted = sorted(
            f"{name} -> {model}" for name, (model, origin) in resolved.items() if origin == "compose-default"
        )
        if defaulted:
            problems.append(
                f"brain(s) taking a model from a compose DEFAULT rather than .env: {', '.join(defaulted)} — "
                f"those defaults name a vendor this deployment may have left"
            )

        # 3. Genuinely unset. Only reachable where compose uses ${VAR:?}, which
        #    already refuses to start, so this is reported rather than failed.
        unset = sorted(name for name, (model, origin) in resolved.items() if origin == "unset")
        if not seen and not defaulted:
            model_check = "brain models unresolvable"
        elif problems:
            model_check = "brain models INCONSISTENT"
        else:
            distinct = ", ".join(f"{n}={m}" for n, (m, _) in sorted(resolved.items()) if m)
            model_check = f"{len(seen)} distinct brain models ({distinct})"
            if unset:
                model_check += f"; {', '.join(unset)} unset (compose refuses to start)"

    if len(brains) < 2:
        problems.append(
            f"only {len(brains)} brain channel(s) found ({', '.join(brains) or 'none'}) — "
            f"a shadow run with one brain is not comparing anything"
        )

    # The wake contract. The judge answers "does a person need to act NOW" per
    # verdict, and the deployment quiets an explicit "no" — that pair is easy to
    # break from either end without noticing: drop the extracted field and the
    # filter matches nothing (every card delivers again, silently); loosen the
    # match to a list or a `contains` and '' — the unanswered rows, every
    # pre-wake verdict and every parse failure — starts matching too, which
    # turns fail-open into fail-silent. Both ends are asserted, and the match
    # must be the exact string "no".
    notify = cfg.sources.get("judge-notify")
    quiet = next((st for st in cfg.pipeline if st.type == "filter" and st.options.get("when", {}).get("wake")), None)
    if notify is not None and "wake" in notify.fields:
        if quiet is None:
            problems.append("judge-notify extracts `wake` but no filter stage quiets on it — the field is decoration")
        else:
            when = quiet.options.get("when") or {}
            if when.get("wake") != "no":
                problems.append(
                    f"the wake filter matches {when.get('wake')!r} — it must be the exact string 'no', "
                    f"or unanswered ('') verdicts stop failing open into a card"
                )
            if when.get("source") != "judge-notify":
                problems.append("the wake filter is not pinned to source judge-notify — it would drop other sources' events")
    elif quiet is not None:
        problems.append("a filter quiets on `wake` but judge-notify does not extract it — the stage can never match")

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
        f"{len(cfg.channels)} channel(s) all in-network, {len(brains)} brains fed, "
        f"{model_check}, {reach}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
