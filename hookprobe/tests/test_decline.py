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
