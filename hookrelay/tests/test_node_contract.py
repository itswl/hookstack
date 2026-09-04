"""The checker that watches a node keep the promises its brief made.

Both fixtures are REAL rounds from 2026-09-04, not invented scenarios, which is
the difference between a golden set that pins behaviour and one that pins
somebody's idea of behaviour. The failing pair is the 16:40 round that posted a
signal and moved neither cursor; the passing pair is the 16:20 round twenty
minutes earlier that did the same work correctly.

Lives under hookrelay/tests because that is the venv the stack gate runs, and
the checker reads the pipe's ledger. It asserts nothing about hookrelay itself.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
CHECKER = ROOT / "scripts" / "assert_node_contract.py"
FIX = ROOT / "scripts" / "fixtures" / "node-contract"


def run(before: str, after: str, ledger: str, since: str, cursors: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(CHECKER),
            "--before",
            str(FIX / before),
            "--after",
            str(FIX / after),
            "--ledger",
            str(FIX / ledger),
            "--since",
            since,
            "--source",
            "watch",
            *(["--cursors", str(FIX / cursors)] if cursors else []),
        ],
        capture_output=True,
        text=True,
    )


def test_the_round_that_posted_a_signal_and_moved_nothing_is_caught() -> None:
    """2026-09-04 16:40. It sent a card for BCP-SRE and left both cursors at
    16:20:31, so the next round re-read the same baseline and sent the same
    message again. Nothing in the system noticed for four hours."""
    out = run("stalled-before.json", "stalled-after.json", "stalled-ledger.json", "1788510500")
    assert out.returncode == 1, out.stdout
    assert "BCP-SRE" in out.stdout, "the violation must name the conversation, not just the count"
    assert "cursor moved forward" in out.stdout


def test_the_same_work_done_correctly_passes() -> None:
    """2026-09-04 16:20, twenty minutes earlier: same conversation, same shape of
    signal, cursors advanced. A checker that cannot tell these two apart would be
    worse than none — it would make every round look broken."""
    out = run("ok-before.json", "ok-after.json", "ok-ledger.json", "1788509000")
    assert out.returncode == 0, out.stdout
    # Named, not counted. The count was pinned here once and it broke the day a
    # promise stopped being one anybody makes — which taught the wrong lesson,
    # because what a test should defend is that the round PASSED for the right
    # reason, not that the checker still has the same number of reasons.
    assert "kept its promises" in out.stdout
    assert "cursor moved forward" in out.stdout


def test_reporting_further_than_you_have_read_is_impossible() -> None:
    """Structural, and true at every instant rather than only after a round."""
    bad = FIX / "_tmp-impossible.json"
    bad.write_text(json.dumps({"feeds": {"X": 100.0}, "reported": {"X": 200.0}}))
    try:
        out = run("ok-before.json", "_tmp-impossible.json", "ok-ledger.json", "1788509000")
        assert out.returncode == 1
        assert "reported further than it has been read" in out.stdout
    finally:
        bad.unlink()


def test_it_cannot_report_a_conversation_nothing_offered_it() -> None:
    """The promise the split made checkable. `feeds` is now written by the
    scanner and `reported` by the node, so the scan file also records what it
    handed over that round; a signal naming anything else is a conversation name
    copied wrong — which would make the FIRST promise go quiet rather than fail,
    since it matches subjects by that same name."""
    scan = FIX / "_tmp-scan.json"
    scan.write_text(json.dumps({"feeds": {"BCP-SRE": 1788510031.0}, "offered": {"Somewhere Else": 1.0}}))
    try:
        out = run("ok-before.json", "ok-after.json", "ok-ledger.json", "1788509000", "_tmp-scan.json")
        assert out.returncode == 1, out.stdout
        assert "the scan offered it" in out.stdout
        assert "BCP-SRE" in out.stdout, "name the conversation it invented"
    finally:
        scan.unlink()


def test_a_scan_that_offered_it_is_not_a_violation() -> None:
    """The other half, without which the check above would pass by always failing."""
    scan = FIX / "_tmp-scan-ok.json"
    scan.write_text(json.dumps({"feeds": {"BCP-SRE": 1788510031.0}, "offered": {"BCP-SRE": 1788510031.0}}))
    try:
        out = run("ok-before.json", "ok-after.json", "ok-ledger.json", "1788509000", "_tmp-scan-ok.json")
        assert out.returncode == 0, out.stdout
        assert "the scan offered it" in out.stdout
    finally:
        scan.unlink()


def test_a_quiet_round_is_not_a_violation() -> None:
    """No signals means nothing to promise about. The checker must not treat
    silence as failure — most rounds are silent, and a checker that cried on
    every one of them would be switched off within a day."""
    out = run("ok-after.json", "ok-after.json", "stalled-ledger.json", "9999999999")
    assert out.returncode == 0, out.stdout
