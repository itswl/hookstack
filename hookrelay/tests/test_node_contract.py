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


def test_a_stale_offer_is_a_skipped_promise_not_a_broken_one() -> None:
    """`scan.json` has one slot and every tick overwrites it, including ticks
    whose round is skipped — so the moment a round is skipped, the offer that
    justified the previous round's signals is gone and what remains describes a
    round that posted nothing.

    Measured on 2026-09-09: a round fired at 11:40 and reported a conversation,
    the next three ticks had nothing to do and rewrote `offered` to empty, and
    the check accused that one round once every twenty minutes against an offer
    made eighty-nine minutes after it.
    """
    scan = FIX / "_tmp-scan-stale.json"
    scan.write_text(json.dumps({"feeds": {"BCP-SRE": 1788510031.0}, "offered": {}, "round_at": 1788599999.0}))
    try:
        out = run("ok-before.json", "ok-after.json", "ok-ledger.json", "1788509000", "_tmp-scan-stale.json")
        assert out.returncode == 0, out.stdout
        assert "describes a later round" in out.stdout, "and it says why it skipped, rather than going quiet"
        assert "the scan offered it" not in out.stdout
    finally:
        scan.unlink()


def test_an_offer_from_the_same_round_is_still_checked() -> None:
    """The other half: skipping on staleness must not skip everything."""
    scan = FIX / "_tmp-scan-fresh.json"
    scan.write_text(json.dumps({"feeds": {"BCP-SRE": 1788510031.0}, "offered": {}, "round_at": 1788508000.0}))
    try:
        out = run("ok-before.json", "ok-after.json", "ok-ledger.json", "1788509000", "_tmp-scan-fresh.json")
        assert out.returncode == 1, out.stdout
        assert "the scan offered it" in out.stdout
    finally:
        scan.unlink()


def test_the_checker_does_not_read_its_own_violations_as_conversations() -> None:
    """A violation travels as a signal on the same source the node uses, so
    `patrol-timer / contract` lands in the ledger looking like a conversation
    the node reported — and `contract` is never a conversation any scan offered.

    Left alone, one false positive became two subjects at the next tick and
    stayed there: a detector whose own output is its next input does not report
    a problem, it becomes one.
    """
    ledger = FIX / "_tmp-ledger-self.json"
    ledger.write_text(
        json.dumps(
            {
                "recent": [
                    {"source": "watch", "received_at": 1788510031.0, "fields": {"origin": "chat / BCP-SRE"}},
                    {"source": "watch", "received_at": 1788510040.0, "fields": {"origin": "patrol-timer / contract"}},
                ]
            }
        )
    )
    scan = FIX / "_tmp-scan-self.json"
    scan.write_text(json.dumps({"feeds": {"BCP-SRE": 1788510031.0}, "offered": {"BCP-SRE": 1788510031.0}}))
    try:
        out = run("ok-before.json", "ok-after.json", "_tmp-ledger-self.json", "1788509000", "_tmp-scan-self.json")
        assert "contract" not in out.stdout.split("promise")[0], "its own signal is not a conversation"
        assert "1 signal(s) across 1 conversation(s)" in out.stdout
    finally:
        ledger.unlink()
        scan.unlink()
