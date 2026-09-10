#!/usr/bin/env python3
"""A knob no compose passes through is a knob nobody can turn.

There is no `env_file` in the deployed composes, deliberately (2026-09-02): the
`environment:` block IS the complete list of what a container can see. So a
setting the code reads, the reference table documents and no compose names is
not a setting at all — it is a default with a docstring, and the operator who
sets it in `.env` changes nothing.

That has now happened four times. `HOOKPROBE_PRICE_*` shipped this way and the
ledger went on pricing from the runtime's own table while `.env` said otherwise.
On 2026-09-10 three more landed in one afternoon — a high-risk allowlist, a
remediation cooldown and a selftest interval — two of them written by the same
session that had just read the comment warning about it. `deploy_preflight.py`
checks the OPPOSITE direction (a variable in `.env` that no compose passes), so
none of the four was caught: nobody had set them in `.env`, because they were
new.

    python3 scripts/assert_knobs_are_reachable.py          # every service
    python3 scripts/assert_knobs_are_reachable.py --list    # what is reachable

A knob is reachable if ANY compose in the repository passes it through — the
quickstart and the work stack count, because a knob an operator can set on some
deployment is a knob, and one nothing can set is a lie in the reference table.
Anything else must be listed in NOT_A_KNOB with the reason it is not settable.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Where each service reads its environment. One file each, on purpose: a service
# that read env vars from three modules would need this list kept in step, and
# the whole point here is a rule with one home.
# Where each service reads its environment. hookrelay is a LIST because it does
# not have one home for this — `settings.py`, `alarm.py`, `registry.py`,
# `app.py` and `__main__.py` each read their own — and the floor below caught
# that on this checker's first run: pointed at `config.py` alone it found zero
# and reported nothing wrong, which is the silent-check failure it was written
# to prevent, in the checker itself.
SETTINGS = (
    ("hookprobe", "hookprobe/hookprobe/settings.py"),
    ("hookrelay", "hookrelay/hookrelay/settings.py"),
    ("hookrelay", "hookrelay/hookrelay/alarm.py"),
    ("hookrelay", "hookrelay/hookrelay/registry.py"),
    ("hookrelay", "hookrelay/hookrelay/app.py"),
    ("hookrelay", "hookrelay/hookrelay/__main__.py"),
    ("hookjudge", "hookjudge/hookjudge/settings.py"),
)

ENV_NAME = re.compile(r'"((?:HOOKPROBE|HOOKRELAY|HOOKJUDGE)_[A-Z0-9_]+)"')
# In a compose the name appears bare (`HOOKPROBE_X: ${HOOKPROBE_X:-}`), not quoted.
COMPOSE_NAME = re.compile(r"((?:HOOKPROBE|HOOKRELAY|HOOKJUDGE)_[A-Z0-9_]+)")

# Names the code reads that an operator is NOT meant to set through a compose.
# Each needs a reason, and "we forgot" is not one of them — that is the finding
# this script exists to make, not a category to file it under.
# A floor per service, because a checker that stops SEEING is indistinguishable
# from one with nothing to report. `assert_docs.py` went quiet this same day —
# its boundary-count regex enumerated number words, "twenty-six" matched none of
# them, and two pages dropped out of the comparison while the gate printed a
# verified-looking number it had checked on one page of three. A parse that
# returns fewer names than this is a parse that has lost the file, and it fails
# loudly rather than passing on a short list.
FLOOR = {"hookprobe": 60, "hookrelay": 18, "hookjudge": 20}

NOT_A_KNOB: dict[str, str] = {
    "HOOKPROBE_AGENT_TOKEN": (
        "generated per process when empty, and that is the shipping posture: nothing to configure, "
        "nothing on disk, and it rotates on restart. Set it only if something outside the container "
        "needs the read-only surface, which no deployment here does"
    ),
    "HOOKRELAY_CARD_CALLBACK_SECRET": (
        "an OPTIONAL second signature on /card-action, and unusable on the deployments this repo "
        "ships: the only client that posts there is the lark bridge, which sends no signature header "
        "(bridge.py, the card-action POST carries content-type and nothing else), so setting it 401s "
        "every button press. The control that always applies is the signed single-use token in the "
        "button itself. Passing it through cost a refused deploy on 2026-09-10 — `deploy_preflight` "
        "requires any _SECRET a deployed compose names to be non-empty, and following its advice to "
        "set one would have broken the button path. Add the line, and the bridge's signing, together"
    ),
    # The alternate runtimes' binaries and config paths. Their defaults are the
    # paths inside the image this repository builds, so on every deployment here
    # they are already correct; only a custom image needs to move them, and that
    # image arrives with its own compose. `HOOKPROBE_RUNTIME` — which runtime
    # answers at all — IS a knob and is passed through.
    **{
        name: "an image-internal path, correct for the image this repo builds; a custom image brings its own compose"
        for name in (
            "HOOKPROBE_CODEX_BINARY",
            "HOOKPROBE_CODEX_CONFIG",
            "HOOKPROBE_CODEX_PYTHON",
            "HOOKPROBE_PI_BINARY",
            "HOOKPROBE_PI_CONFIG",
            "HOOKPROBE_PI_PROVIDER",
            "HOOKPROBE_PI_PYTHON",
        )
    },
}


def knobs_read() -> dict[str, str]:
    """Every environment name a service's settings module reads, and whose."""
    found: dict[str, str] = {}
    for service, path in SETTINGS:
        try:
            text = (ROOT / path).read_text(encoding="utf-8")
        except OSError:
            continue
        for name in ENV_NAME.findall(text):
            found.setdefault(name, service)
    return found


def knobs_passed() -> set[str]:
    """Every environment name any compose in the repository passes through."""
    passed: set[str] = set()
    for compose in sorted(ROOT.glob("**/docker-compose*.yml")):
        if ".venv" in compose.parts or "node_modules" in compose.parts:
            continue
        passed |= set(COMPOSE_NAME.findall(compose.read_text(encoding="utf-8")))
    return passed


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="print every knob and whether a compose passes it")
    args = parser.parse_args(argv[1:])

    read, passed = knobs_read(), knobs_passed()
    if args.list:
        for name, service in sorted(read.items()):
            state = "reachable" if name in passed else ("not-a-knob" if name in NOT_A_KNOB else "UNREACHABLE")
            print(f"  {state:12} {service:10} {name}")
        return 0

    thin = [
        f"{service}: found {sum(1 for n, s in read.items() if s == service)} settings, expected at least {floor}"
        for service, floor in FLOOR.items()
        if sum(1 for n, s in read.items() if s == service) < floor
    ]
    if thin:
        print("knobs: the parse has lost sight of a settings file — this check is not checking:", file=sys.stderr)
        for line in thin:
            print(f"  {line}", file=sys.stderr)
        print("\nFix the parse, or lower the floor in this file with the reason the count dropped.", file=sys.stderr)
        return 1
    stale = sorted(name for name in NOT_A_KNOB if name not in read)
    unreachable = sorted(name for name in read if name not in passed and name not in NOT_A_KNOB)
    if stale:
        print("knobs: NOT_A_KNOB names a setting the code no longer reads:", file=sys.stderr)
        for name in stale:
            print(f"  {name}", file=sys.stderr)
        return 1
    if unreachable:
        print(
            f"knobs: {len(unreachable)} setting(s) the code reads that NO compose passes through — "
            "an operator setting these in .env changes nothing:",
            file=sys.stderr,
        )
        for name in unreachable:
            print(f"  {read[name]:10} {name}", file=sys.stderr)
        print(
            "\nAdd it to the `environment:` block of the compose that should carry it, or to "
            "NOT_A_KNOB in this file with the reason an operator cannot set it.",
            file=sys.stderr,
        )
        return 1
    exempt = f", {len(NOT_A_KNOB)} deliberately not settable" if NOT_A_KNOB else ""
    print(f"knobs: every one of {len(read)} settings a service reads is reachable from a compose{exempt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
