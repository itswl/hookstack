"""The families this node has no instrument for are declined at the door.

Measured before it existed: the platform upstream had excluded four alert
families from investigation on 2026-09-01 because the investigator cannot reach
their cloud account, and the pipe's own escalation leg then funded twenty
investigations of one of them in a week — 70% of the investigator's spend, each
report able only to restate the alert. Every clause here is a way the list
could be wrong: declining by substring, declining a person, dying on a typo.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from hookprobe import decline
from hookprobe.app import create_app
from hookprobe.runs import RunStore
from hookprobe.service import RunService
from tests.helpers import FakeEngine, make_settings

TOKEN = "secret-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
EVENT = {
    "title": "[MAIL] bounce rate 9% — provider review threshold",
    "body": "bounces 271 in the last hour",
    "level": "high",
    "source": "inbound",
    "event_id": 5,
    "fields": {"env": "prod"},
}


def _list(tmp_path: Path, *lines: str) -> Path:
    path = tmp_path / "decline.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _client(tmp_path: Path, engine: FakeEngine, **overrides: object) -> TestClient:
    settings = make_settings(tmp_path, token=TOKEN, **overrides)
    return TestClient(create_app(settings, RunService(settings, engine, RunStore(tmp_path / "results"))))


# ── the list itself ───────────────────────────────────────────────────────────


def test_unset_or_missing_declines_nothing(tmp_path):
    assert decline.decline_reason(None, EVENT["title"]) == ""
    assert decline.decline_reason(tmp_path / "absent.txt", EVENT["title"]) == ""


def test_full_match_never_search(tmp_path):
    """A pattern has to name the whole title. `MAIL` alone declines nothing —
    a substring list would decline every title that happened to contain a word."""
    path = _list(tmp_path, "MAIL", r"\[MAIL\].*")
    assert decline.decline_reason(path, EVENT["title"]) == r"\[MAIL\].*"
    assert decline.decline_reason(_list(tmp_path, "MAIL"), EVENT["title"]) == ""
    assert decline.decline_reason(path, "[QUEUE] broker CPU > 80%") == ""


def test_comments_blanks_and_a_typo_are_skipped_not_fatal(tmp_path, caplog):
    """The list fails OPEN: a bad line is logged and ignored, the good lines still
    apply, and nothing is declined by accident because of it."""
    path = _list(tmp_path, "# the mail family", "", r"\[MAIL\](.*", r"\[QUEUE\].*")
    with caplog.at_level("WARNING", logger="hookprobe.decline"):
        assert decline.decline_reason(path, "[QUEUE] broker CPU > 80%") == r"\[QUEUE\].*"
        assert decline.decline_reason(path, EVENT["title"]) == "", "the broken line declines nothing"
    assert any("line 3" in record.getMessage() and "ignored" in record.getMessage() for record in caplog.records)


def test_the_list_is_read_on_every_event(tmp_path):
    path = _list(tmp_path, r"\[QUEUE\].*")
    assert decline.decline_reason(path, EVENT["title"]) == ""
    path.write_text(r"\[MAIL\].*" + "\n", encoding="utf-8")
    assert decline.decline_reason(path, EVENT["title"]) == r"\[MAIL\].*", "no restart, no cache"


# ── at the door ───────────────────────────────────────────────────────────────


def test_a_declined_family_opens_no_run_and_says_why(tmp_path):
    engine = FakeEngine()
    with _client(tmp_path, engine, decline_patterns=_list(tmp_path, r"\[MAIL\].*")) as client:
        answer = client.post("/hooks/event", json=EVENT).json()
        assert answer["status"] == "skipped"
        assert "no instrument" in answer["reason"] and answer["pattern"] == r"\[MAIL\].*"
        assert engine.calls == 0
        assert client.get("/v1/runs", headers=AUTH).json() == []

        other = client.post("/hooks/event", json=dict(EVENT, title="disk 88% on cache-3", event_id=6)).json()
        assert other["status"] == "accepted", "a title the list does not name is investigated as before"


def test_a_person_asking_from_chat_is_never_declined(tmp_path):
    """The list is about rule-driven escalations. Somebody who typed the request
    has said they want a run, and the door already gates who may do that."""
    engine = FakeEngine()
    with _client(
        tmp_path, engine, decline_patterns=_list(tmp_path, r"\[MAIL\].*"), follow_up_senders=frozenset({"*"})
    ) as client:
        asked = dict(EVENT, event_id=7, fields={"sender": "ou_person"})
        assert client.post("/hooks/event", json=asked).json()["status"] == "accepted"


def test_the_level_bar_still_comes_first(tmp_path):
    """Cheapest refusal first, and the one every deployment already has: a
    below-bar title is skipped for its level, whatever the list says."""
    with _client(tmp_path, FakeEngine(), decline_patterns=_list(tmp_path, r"\[MAIL\].*")) as client:
        answer = client.post("/hooks/event", json=dict(EVENT, level="info")).json()
        assert answer["status"] == "skipped" and "below escalation bar" in answer["reason"]


def test_a_decline_is_a_ledger_line_and_the_tally_is_three_valued(tmp_path):
    """The saving used to be a log line nobody greps. A window's count of
    declines, per pattern, is what the weekly page prints — and "no list" has to
    read differently from "the list matched nothing"."""
    listed = _list(tmp_path, r"\[MAIL\].*", r"\[MQ\].*")
    now = 1_000_000.0
    decline.record(tmp_path, "[MAIL] bounce 9%", r"\[MAIL\].*", at=now - 60)
    decline.record(tmp_path, "[MAIL] bounce 9%", r"\[MAIL\].*", at=now - 120)
    decline.record(tmp_path, "[MQ] consumers 0", r"\[MQ\].*", at=now - 180)
    decline.record(tmp_path, "[MAIL] complaint 0.3%", r"\[MAIL\].*", at=now - 10 * 86400)
    with (tmp_path / "declines.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("not json\n")

    week = decline.tally(tmp_path, listed, since=now - 7 * 86400)
    assert week == {
        "configured": True,
        "patterns": 2,
        "declined": 3,
        "conditions": 2,
        "by_pattern": {r"\[MAIL\].*": 2, r"\[MQ\].*": 1},
    }, "the old row is outside the window and the bad line is skipped, not fatal"

    unlisted = decline.tally(tmp_path, None, since=now - 7 * 86400)
    assert unlisted["configured"] is False and unlisted["patterns"] == 0
    assert unlisted["declined"] == 3, "the ledger still counts what an earlier list did"

    empty = decline.tally(tmp_path, _list(tmp_path, "# nothing yet"), since=now - 7 * 86400)
    assert empty["configured"] is True and empty["patterns"] == 0, "a list that is set but empty is visible as such"


def test_the_door_records_what_it_declined_and_the_route_counts_it(tmp_path):
    engine = FakeEngine()
    with _client(tmp_path, engine, decline_patterns=_list(tmp_path, r"\[MAIL\].*")) as client:
        assert client.get("/v1/declines").status_code == 401, "a read of the tally is behind the console bearer"
        before = client.get("/v1/declines", headers=AUTH).json()
        assert before["configured"] is True and before["declined"] == 0 and before["hours"] == 168

        client.post("/hooks/event", json=EVENT)
        client.post("/hooks/event", json=dict(EVENT, event_id=6))
        client.post("/hooks/event", json=dict(EVENT, event_id=7, title="[MAIL] complaint rate 0.3%"))
        client.post("/hooks/event", json=dict(EVENT, event_id=8, title="disk 88% on cache-3"))

        after = client.get("/v1/declines?hours=1", headers=AUTH).json()
        assert after["declined"] == 3 and after["conditions"] == 2 and after["hours"] == 1
        assert after["by_pattern"] == {r"\[MAIL\].*": 3}
        assert engine.calls == 1, "the one title the list does not name was investigated"
        lines = (tmp_path / "declines.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(lines) == 3 and all('"pattern"' in line for line in lines)


def test_without_a_list_the_tally_says_there_is_no_list(tmp_path):
    with _client(tmp_path, FakeEngine()) as client:
        body = client.get("/v1/declines", headers=AUTH).json()
        assert body["configured"] is False and body["patterns"] == 0 and body["declined"] == 0
