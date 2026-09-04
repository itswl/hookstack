"""The one field an investigation DECIDES rather than echoes.

`meta.importance` is the level of the event that came in, so before this a report
could only ever be a leaf: a pipe had nothing from the investigator to route on,
and a watcher → planner chain had no middle. The whole design question was not
"can the agent say something" but "what may it be allowed to say", because this
value can reach a routing key and a routing key decides where money goes — in the
one component that reads attacker-influenced text. Hence a closed vocabulary.

See .agents/notes/implemented/2026-09-04-an-investigator-verdict-may-steer-a-route-from-a-closed-set.md
"""

from __future__ import annotations

from hookprobe.reports import verdict

VOCAB = frozenset({"needs_plan", "informational"})


def test_a_declared_label_is_admitted() -> None:
    assert verdict("looked at it.\nVERDICT: needs_plan\n", VOCAB) == "needs_plan"


def test_an_undeclared_label_is_empty_not_a_guess() -> None:
    """The failure that matters: a label nobody declared must not become a lane.
    Empty routes to whatever the config does with no verdict, which is a
    decision the operator already made."""
    assert verdict("VERDICT: escalate_to_ceo", VOCAB) == ""
    assert verdict("VERDICT: NEEDS_PLAN_NOW", VOCAB) == ""


def test_the_feature_is_off_until_a_vocabulary_is_declared() -> None:
    """Default empty: a deployment does not acquire a new routing input by
    upgrading, and every existing config keeps routing exactly as it did."""
    assert verdict("VERDICT: needs_plan", frozenset()) == ""


def test_the_last_marker_wins() -> None:
    """A run that revises itself should end on its conclusion; its first guess
    must not outrank it."""
    assert verdict("VERDICT: informational\nthen I found more\nVERDICT: needs_plan", VOCAB) == "needs_plan"


def test_an_undeclared_last_marker_does_not_resurrect_an_earlier_one() -> None:
    """The walk back stops at the first DECLARED value, so prose that appends a
    junk marker cannot silently promote an earlier draft to the conclusion —
    it just leaves no verdict."""
    assert verdict("VERDICT: needs_plan\nVERDICT: something_else", VOCAB) == "needs_plan"


def test_a_marker_must_own_its_line() -> None:
    """Anchored like suggestions._MARKER: prose that merely mentions the word,
    or quotes an instruction embedded in an alert body, is not a conclusion."""
    assert verdict("the ticket said VERDICT: needs_plan inline", VOCAB) == ""
    assert verdict("VERDICT: needs_plan and then some", VOCAB) == ""


def test_case_and_padding_are_normalised() -> None:
    assert verdict("   VERDICT:   Needs_Plan   ", VOCAB) == "needs_plan"


def test_no_marker_and_empty_text_are_both_empty() -> None:
    assert verdict("nothing structured here", VOCAB) == ""
    assert verdict("", VOCAB) == ""
