#!/usr/bin/env python3
"""Break each containment claim on purpose, and require a test to notice.

A green suite proves the code passes its tests. It does not prove the tests
would catch the code being WRONG — and this repository's recurring bug shape is
exactly that: a boundary described everywhere and absent in fact. `gate.verify`
exists because a gate that could not answer looked identical to one that did;
`/v1/selftest` exists because `/v1/agent` reported a posture nothing checked.

This is the same argument aimed at the suite itself. Each entry below deletes or
widens one guarantee, runs the tests that claim to protect it, and requires them
to FAIL. A mutation that survives is a guarantee nobody is really testing, and
the message says which.

The gate already carries one inverted check of this kind — `assert_node_contract`
must fail on a round it was written to catch — for the same reason: a checker
that has quietly stopped catching anything looks exactly like one with nothing
to catch.

    python3 scripts/assert_guards_are_tested.py            # every mutation
    python3 scripts/assert_guards_are_tested.py --list     # names only

WHAT THIS IS NOT. It is not mutation coverage. Seven modules and a hand-written
list is not a proof about the suite; it is a spot check on the claims that would
hurt most if their tests were decorative. Add one whenever a boundary is added,
which is the whole ceremony.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess  # nosec B404 — runs this repo's own pytest, fixed argv
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = ROOT / "hookprobe" / ".venv" / "bin" / "python"

# (name, file, exact text to replace, replacement, tests that must then fail)
MUTATIONS: tuple[tuple[str, str, str, str, tuple[str, ...]], ...] = (
    (
        "the read-only guard allows everything",
        "hookprobe/hookprobe/guard.py",
        "    for pattern, label in _DENY_RULES:",
        "    for pattern, label in ():",
        ("tests/test_guard.py", "tests/test_guard_postures.py"),
    ),
    (
        "the egress boundary can be switched off from the shell",
        "hookprobe/hookprobe/guard.py",
        "    for pattern, label in _ALWAYS_RULES:",
        "    for pattern, label in ():",
        ("tests/test_guard.py",),
    ),
    (
        "`aws` matches any occurrence again, not the command",
        "hookprobe/hookprobe/guard.py",
        'rf"(?<!\\.)\\baws\\b(?!\\.)(?![^|;&\\n]*{_AWS_READ})"',
        'rf"\\baws\\b(?![^|;&\\n]*{_AWS_READ})"',
        ("tests/test_guard.py",),
    ),
    (
        "the service's secrets are handed to the agent",
        "hookprobe/hookprobe/gate.py",
        "SECRETS_WITHHELD_FROM_AGENT = (",
        "SECRETS_WITHHELD_FROM_AGENT = () if True else (",
        ("tests/test_hygiene.py", "tests/test_runtime_contract.py"),
    ),
    (
        "the MCP gate allows any tool",
        "hookprobe/hookprobe/gate.py",
        "def mcp_deny_reason(tool_name: str, allowed: frozenset[str]) -> str | None:",
        "def mcp_deny_reason(tool_name: str, allowed: frozenset[str]) -> str | None:\n    return None",
        ("tests/test_mcp_guard.py", "tests/test_runtime_contract.py"),
    ),
    (
        "the credential detector never fires",
        "hookprobe/hookprobe/gate.py",
        "_SECRET_SHAPES: tuple[tuple[str, re.Pattern[str]], ...] = (",
        "_SECRET_SHAPES: tuple[tuple[str, re.Pattern[str]], ...] = () if True else (",
        ("tests/test_output_guard.py",),
    ),
    (
        "the audit chain stops linking its lines",
        "hookprobe/hookprobe/audit.py",
        '            linked = {**line, "prev": previous}',
        '            linked = {**line, "prev": _CHAIN_SEED}',
        ("tests/test_audit_chain.py",),
    ),
    (
        "the agent's bearer is accepted on writes",
        "hookprobe/hookprobe/app.py",
        '            request.method == "GET"',
        '            request.method in ("GET", "PUT", "POST")',
        ("tests/test_runtime_contract.py",),
    ),
    (
        "approved commands run without the allowlist",
        "hookprobe/hookprobe/remediation.py",
        "        reason = step_deny_reason(step, patterns, high_risk)",
        "        reason = None",
        ("tests/test_remediation.py",),
    ),
    (
        "a high-risk step runs on the ordinary allowlist alone",
        "hookprobe/hookprobe/remediation.py",
        '    if str(step.get("risk") or "").strip().lower() != "high":',
        "    if True:",
        ("tests/test_remediation.py",),
    ),
    (
        "an approved command inherits the service's secrets",
        "hookprobe/hookprobe/remediation.py",
        "                env=execution_env(),\n",
        "",
        ("tests/test_remediation.py",),
    ),
    (
        "the approval window never expires",
        "hookprobe/hookprobe/remediation.py",
        "APPROVAL_WINDOW_SECONDS = 24 * 3600",
        "APPROVAL_WINDOW_SECONDS = 24 * 3600 * 100000",
        ("tests/test_remediation.py", "tests/test_work.py"),
    ),
    (
        "the freshness cursor never moves",
        "hookprobe/hookprobe/remediation.py",
        "    if not before or not now:",
        "    if True or not before or not now:",
        ("tests/test_remediation.py", "tests/test_card_actions.py"),
    ),
    (
        "one target can be changed twice in a minute",
        "hookprobe/hookprobe/remediation.py",
        "COOLDOWN_SECONDS = 900",
        "COOLDOWN_SECONDS = 0",
        ("tests/test_remediation.py", "tests/test_card_actions.py"),
    ),
    (
        "the selftest counts a check it could not run as a pass",
        "hookprobe/hookprobe/selftest.py",
        '    ran = [c for c in checks if c["held"] is not None]',
        '    ran = [c for c in checks if c["held"] is True]',
        ("tests/test_selftest.py",),
    ),
)


def survives(file: str, before: str, after: str, tests: tuple[str, ...]) -> str | None:
    """Apply one mutation, run its tests, restore. None when the tests caught it."""
    path = ROOT / file
    original = path.read_text(encoding="utf-8")
    if original.count(before) != 1:
        return f"anchor appears {original.count(before)} times — this entry has rotted"
    with tempfile.TemporaryDirectory() as keep:
        backup = Path(keep) / path.name
        shutil.copy2(path, backup)
        try:
            path.write_text(original.replace(before, after, 1), encoding="utf-8")
            done = subprocess.run(  # nosec B603 — fixed argv, this repo's own venv
                [str(PY), "-m", "pytest", "-q", "-x", *tests],
                cwd=ROOT / "hookprobe",
                capture_output=True,
                text=True,
                timeout=600,
                # A CACHE OF ITS OWN, and this is not tidiness — it is the
                # difference between this file measuring something and measuring
                # nothing. Python validates a `.pyc` against the source's
                # (mtime-truncated-to-SECONDS, size). Two mutations of the same
                # module that add the SAME NUMBER OF CHARACTERS and land in the
                # same wall-clock second are indistinguishable to that check, so
                # the second run silently executes the first one's bytecode.
                #
                # It happened here: emptying SECRETS_WITHHELD_FROM_AGENT and
                # emptying _SECRET_SHAPES both add exactly 16 characters to
                # gate.py, and the credential detector was intermittently
                # reported ESCAPED while running the withheld-list mutation's
                # code. The false alarm is the safe direction; the same race can
                # report `caught` for a guarantee that was never tested, which
                # is not.
                env={**os.environ, "PYTHONPYCACHEPREFIX": str(Path(keep) / "pycache")},
            )
        finally:
            shutil.copy2(backup, path)
    return None if done.returncode != 0 else "the tests passed with it broken"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="print the claims and exit")
    args = parser.parse_args()
    if args.list:
        for name, *_ in MUTATIONS:
            print(f"  {name}")
        return 0
    if not PY.is_file():
        print(f"no interpreter at {PY} — run hookprobe's one-time venv setup", file=sys.stderr)
        return 1

    escaped: list[str] = []
    for name, file, before, after, tests in MUTATIONS:
        why = survives(file, before, after, tests)
        if why:
            escaped.append(f"{name}: {why}")
            print(f"  ESCAPED  {name}", file=sys.stderr)
        else:
            print(f"  caught   {name}")
    if escaped:
        print("\nA mutation that survives is a guarantee nobody is really testing:", file=sys.stderr)
        for line in escaped:
            print(f"  {line}", file=sys.stderr)
        print(
            "\nEither the test that should catch it is asserting something weaker than it\n"
            "looks, or the entry above no longer describes the code. Both are worth the\n"
            "same fix: make the test fail when the boundary is gone.",
            file=sys.stderr,
        )
        return 1
    print(f"guards: {len(MUTATIONS)} containment claims break their own tests when removed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
