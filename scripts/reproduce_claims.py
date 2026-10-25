#!/usr/bin/env python3
"""Every headline number: reproduce it here, or say out loud that you cannot.

`docs/evidence.md` is the prose table; this is the half of it a machine can
check. The repository's most persuasive numbers come from two deployments on
somebody else's infrastructure, whose ledgers are runtime data and therefore not
in the tree — so the useful thing is not a green tick over all of them, it is a
line that says which class each number is in.

Two classes, and the failure modes are different:

  reproducible  a command in this repository produces the number. THIS script
                runs it and fails if it has stopped agreeing.
  reported      measured on the retired deployments. Nothing here can run it, so
                nothing here claims to. The row names the artefact that would
                back it, because "trust me" and "ask me for this file" are
                different sentences.

    python3 scripts/reproduce_claims.py            # check the reproducible half
    python3 scripts/reproduce_claims.py --list     # the table, changed nothing

WHAT THIS DOES NOT DO. It does not audit the reported numbers — it cannot, and a
script that pretended to would be the worst outcome this file could produce: a
green light over a figure nobody checked. It also does not run the two
pytest-backed claims unless the owning service has a venv; without one it says
so and points at the command, because a check that silently skips is one that
passes by finding nothing.
"""

from __future__ import annotations

import subprocess  # nosec B404 — runs this repository's own checks, fixed argv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _run(args: list[str]) -> tuple[bool, str]:
    """(passed, first line of output). Never raises on a failing check."""
    try:
        result = subprocess.run(  # nosec B603 B607 — fixed argv, this repo's own scripts
            args, cwd=ROOT, capture_output=True, text=True, timeout=180, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    first = (result.stdout.strip() or result.stderr.strip()).splitlines()
    return result.returncode == 0, (first[-1] if first else "")


def _pytest_claim(test_path: str, venv_service: str) -> tuple[bool | None, str]:
    """Run one test file in a service's venv, or say it could not run.

    `venv_service` is the venv to borrow, not necessarily the test's own service —
    the lark bridge has no venv and is tested from hookrelay's, which is what the
    stack gate does too.

    A missing venv is `None` (UNRUN), never `True`: a reader running this on a
    fresh clone gets the honest answer and the command to fix it, and the whole
    repository gate — where the venvs always exist, because the component gates
    built them a moment earlier — gets a real run of both files.
    """
    python = ROOT / venv_service / ".venv" / "bin" / "python"
    if not python.is_file():
        return None, f"no {venv_service}/.venv — run `cd {venv_service} && bash scripts/gate.sh` once, then re-run"
    passed, detail = _run([str(python), "-m", "pytest", "-q", test_path])
    return passed, detail


# (claim, how to check it, artefact for the reader)
REPRODUCIBLE: tuple[tuple[str, str, object], ...] = (
    (
        "hookrelay stays at or under 6,550 source lines",
        "hookrelay/README.md",
        lambda: _run([sys.executable, "scripts/assert_weight.py"]),
    ),
    (
        "hookjudge stays at or under 3,540 source lines",
        "hookjudge/README.md",
        lambda: _run([sys.executable, "scripts/assert_weight.py"]),
    ),
    (
        "every route, knob and containment row is written up or named as not",
        "docs/containment.md",
        lambda: _run([sys.executable, "scripts/assert_docs.py"]),
    ),
    (
        "the three boards share one design language",
        "hookrelay · hookjudge · hookprobe pages",
        lambda: _run([sys.executable, "scripts/assert_design.py"]),
    ),
    (
        "a replayed signed request cannot buy a second verdict",
        "docs/containment.md",
        lambda: _pytest_claim("hookrelay/tests/test_signature_replay.py", "hookrelay"),
    ),
    (
        "an empty BRIDGE_INBOUND_SECRET refuses to start",
        "docs/containment.md",
        lambda: _pytest_claim("deploy/lark-bridge/tests/test_fail_closed.py", "hookrelay"),
    ),
)

REPORTED: tuple[tuple[str, str], ...] = (
    ("795 production alerts; 28 of 29 rules answered identically", "hookjudge/eval/dataset.jsonl (gitignored)"),
    ("the fold sends 55% fewer cards than the rule as shipped", "the retired judge ledger"),
    ("the first real write happened 2026-09-30", "the work deployment's run records and audit log"),
    ("kubectl was absent from the work image for ten days", "the release job history and posture records"),
    ("one HTTP POST from inside a probe reached the chat MCP port", "the 2026-09-30 measurement"),
)


def main() -> int:
    if "--list" in sys.argv:
        for claim, artefact in REPORTED:
            print(f"  reported    {claim}  ·  {artefact}")
        return 0

    failed: list[str] = []
    unrun = 0
    print("reproducible claims (run here):")
    for claim, artefact, check in REPRODUCIBLE:
        passed, detail = check()
        if passed is None:
            mark, unrun = "UNRUN", unrun + 1
        else:
            mark = "OK  " if passed else "FAIL"
        print(f"  {mark}  {claim}")
        print(f"        backed by {artefact}")
        if passed is False:
            failed.append(f"{claim} — {detail}")
            print(f"        {detail}")
        elif passed is None:
            print(f"        {detail}")

    print("\nreported claims (measured on the retired deployments; not in this repository):")
    for claim, artefact in REPORTED:
        print(f"  --    {claim}")
        print(f"        would be backed by {artefact}")

    if failed:
        print(f"\n{len(failed)} reproducible claim(s) no longer hold:")
        for line in failed:
            print(f"  {line}")
        return 1
    tail = f"; {unrun} could not run here (no venv)" if unrun else ""
    print(f"\nevidence: every claim this repository can check still holds{tail}; the rest say so out loud.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
