#!/usr/bin/env python3
"""Every requirements.lock still answers its own requirements.txt.

scripts/relock.sh is manual, so the lock only changes when somebody remembers.
Nothing checked that they had. A floor bumped in a requirements.txt without a
relock stays invisible: the gate keeps installing the old pins, CI agrees, and
the disagreement surfaces weeks later on a rebuild that resolves differently —
at which point "the gate ran against known versions", the entire point of
keeping a lock, is simply not true any more.

THE INVARIANT: every package named in requirements.txt appears in the sibling
requirements.lock at a version satisfying the specifier stated there.

That is deliberately not "the lock is what a resolve would produce today". A
real resolve costs a network round trip per service and minutes per run, so it
would live in CI only, on a schedule, and be skipped locally — and the mistake
this is guarding against is not subtle drift. It is somebody editing a
requirements.txt and not running relock.sh. That mistake leaves evidence on
disk, and reading it costs nothing.

WHAT IT CATCHES
  - a dependency added to requirements.txt and never locked (absent);
  - a floor raised past the pin (httpx>=0.30 while the lock says 0.28.1) — the
    dangerous one, because the Dockerfiles install with `-c requirements.lock`
    and the two now describe builds nobody has run;
  - an exact pin disagreeing with the lock (ruff==0.16.3 vs a locked 0.16.1),
    which is how the linter that passed locally differs from the one in CI.

WHAT IT DOES NOT CATCH — read this before trusting a green result
  - transitive drift: a locked package's own dependencies changing, appearing
    or being dropped upstream. Only a resolve sees that.
  - a newer release existing on PyPI than the lock pins. That is not staleness;
    that is the lock doing its job.
  - extras. uvicorn[standard]>=0.34 checks `uvicorn` only — that the extra's own
    payload (httptools, uvloop, watchfiles, websockets) is present and mutually
    consistent is not verified here.
  - lock entries nothing declares any more. The locks are `pip freeze` output,
    so most rows are transitive by nature and flagging extras would fail on
    every one of them. A lock may say more than the requirements; it may not
    say less.
  - environment markers and platform conditionals; there are none today.

    python3 scripts/assert_locks.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVICES = ("hookrelay", "hookjudge", "hookprobe")

# name[extras] (specifier version)? — the shapes these three files actually use.
# A requirement with no specifier at all (types-PyYAML) still has to be present
# in the lock; only its version goes unchecked.
REQUIREMENT = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)"
    r"(?:\[[^\]]*\])?"
    r"(?:\s*(?P<op>==|>=|>|~=)\s*(?P<version>[A-Za-z0-9._-]+))?\s*$"
)
PIN = re.compile(r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)==(?P<version>[A-Za-z0-9._!+-]+)\s*$")
# Leading numeric release only. Suffixes (rc1, .post1, .dev0) are ignored, so a
# pre-release pin compares as its base version — accepted, because nothing here
# pins one and pretending otherwise would mean vendoring PEP 440.
RELEASE = re.compile(r"^v?(\d+(?:\.\d+)*)")


def canonical(name: str) -> str:
    """PEP 503 name folding: pyyaml and PyYAML, pip-audit and pip_audit, are one
    package. Comparing the spellings verbatim reported both as missing."""
    return re.sub(r"[-_.]+", "-", name).lower()


def release(version: str) -> tuple[int, ...]:
    match = RELEASE.match(version)
    return tuple(int(part) for part in match.group(1).split(".")) if match else ()


def at_least(locked: str, floor: str) -> bool:
    left, right = release(locked), release(floor)
    width = max(len(left), len(right))
    return left + (0,) * (width - len(left)) >= right + (0,) * (width - len(right))


def same(locked: str, pinned: str) -> bool:
    return at_least(locked, pinned) and at_least(pinned, locked)


def check(service: str) -> list[str]:
    txt = ROOT / service / "requirements.txt"
    lock = ROOT / service / "requirements.lock"
    if not lock.exists():
        return [f"{service}: requirements.txt with no requirements.lock — run bash scripts/relock.sh"]

    pins: dict[str, str] = {}
    for line in lock.read_text(encoding="utf-8").splitlines():
        match = PIN.match(line.strip())
        if match:
            pins[canonical(match.group("name"))] = match.group("version")

    problems: list[str] = []
    for number, raw in enumerate(txt.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.split("#")[0].strip()
        if not line or line.startswith("-"):
            continue
        match = REQUIREMENT.match(line)
        if not match:
            # Fail rather than skip: a shape this does not understand is a
            # requirement going unchecked, which is the state being fixed.
            problems.append(f"{service}/requirements.txt:{number}: cannot read {line!r}")
            continue
        name = canonical(match.group("name"))
        locked = pins.get(name)
        if locked is None:
            problems.append(
                f"{service}/requirements.txt:{number}: {match.group('name')} is not in requirements.lock "
                f"— declared but never locked"
            )
            continue
        op, version = match.group("op"), match.group("version")
        if op in (">=", "~=") and not at_least(locked, version):
            problems.append(
                f"{service}/requirements.txt:{number}: {match.group('name')}{op}{version} but the lock pins "
                f"{locked} — the floor moved and the lock did not"
            )
        elif op == ">" and (same(locked, version) or not at_least(locked, version)):
            problems.append(
                f"{service}/requirements.txt:{number}: {match.group('name')}>{version} but the lock pins "
                f"{locked} — the floor moved and the lock did not"
            )
        elif op == "==" and not same(locked, version):
            problems.append(
                f"{service}/requirements.txt:{number}: {match.group('name')} is pinned {version} but the "
                f"lock pins {locked} — two different builds"
            )
    return problems


def main() -> int:
    problems: list[str] = []
    checked = 0
    for service in SERVICES:
        if not (ROOT / service / "requirements.txt").exists():
            problems.append(f"{service}: no requirements.txt")
            continue
        found = check(service)
        problems.extend(found)
        checked += 1

    for problem in problems:
        print(f"  STALE LOCK  {problem}")
    if problems:
        print(f"\n{len(problems)} lock disagreement(s) — after a deliberate bump: bash scripts/relock.sh")
        return 1
    print(f"locks: {checked} services, every declared requirement present and satisfied by its lock")
    return 0


if __name__ == "__main__":
    sys.exit(main())
