"""Which round's offer admits a conversation — the one decision the watch
wrapper makes on its own before it hands the signal to the poster.

The scanner rewrites scan.json on every tick, so a run that outlasts a tick
reads an offer it was never handed. The round before is kept alongside, and a
conversation offered there is admitted with THAT round's cursor; anything
offered in neither is a name copied wrong, refused here rather than left for
the contract checker to accuse next round.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import watch_report  # noqa: E402

SCAN = {
    "round_at": 1000.0,
    "offered": {"ops chat": 11.0},
    "previous": {"round_at": -200.0, "offered": {"old chat": 7.0, "ops chat": 9.0}},
}


def test_this_rounds_offer_wins_and_carries_its_own_cursor():
    assert watch_report.admitted(SCAN, "ops chat") == (11.0, "")


def test_the_round_before_is_admitted_with_the_cursor_it_was_handed():
    assert watch_report.admitted(SCAN, "old chat") == (7.0, "")


def test_a_conversation_offered_in_neither_round_is_refused_naming_both():
    cursor, why = watch_report.admitted(SCAN, "a group nobody scanned")
    assert cursor is None and "ops chat" in why and "old chat" in why


def test_a_quiet_round_after_a_quiet_round_offers_nothing():
    cursor, why = watch_report.admitted({"round_at": 1.0, "offered": {}, "previous": {"offered": {}}}, "ops chat")
    assert cursor is None and why


def test_a_scan_that_states_no_offer_checks_nothing_and_records_nothing():
    """The no-prescan shape: nothing to check against, and no cursor to write."""
    assert watch_report.admitted({"round_at": 1.0}, "ops chat") == (None, "")
    assert watch_report.admitted(None, "ops chat") == (None, "")
