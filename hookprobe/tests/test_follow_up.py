"""A person's reply in the alert's chat thread continues the investigation — gated.

The pipe resolves the thread to a session and forwards the reply as an event
with `fields.kind: follow_up`. The door continues that session (same engine
session, same posture), answers back with `thread_root` so the pipe replies in
the thread, and refuses — with a reason, as a 200 — anyone not on the sender
allowlist, a message it has already answered, and a thread with no session.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from hookprobe import notify
from hookprobe.app import create_app
from hookprobe.runs import RunStore
from hookprobe.service import RunService
from tests.helpers import FakeEngine, make_settings

TOKEN = "secret-token"


def _client(tmp_path: Path, **overrides: Any) -> tuple[TestClient, RunService, FakeEngine]:
    options: dict[str, Any] = {
        "escalate_levels": frozenset({"high", "critical"}),
        "follow_up_senders": frozenset({"ou_sre"}),
        **overrides,
    }
    settings = make_settings(tmp_path, token=TOKEN, **options)
    engine = FakeEngine()
    service = RunService(settings, engine, RunStore(tmp_path / "results"))
    return TestClient(create_app(settings, service)), service, engine


def _wait(client: TestClient, key: str, deadline: float = 3.0) -> dict[str, Any]:
    end = time.time() + deadline
    while time.time() < end:
        r = client.get(f"/v1/runs/{key}", headers={"Authorization": f"Bearer {TOKEN}"})
        if r.status_code == 200 and r.json().get("status") in ("completed", "failed"):
            return r.json()
        time.sleep(0.02)
    raise AssertionError("run did not finish in time")


def _alert(client: TestClient) -> str:
    r = client.post(
        "/hooks/event",
        json={
            "source": "ww",
            "title": "disk 94% on node-3",
            "body": "7% free",
            "level": "high",
            "event_id": 31,
            "fields": {},
        },
    )
    assert r.status_code == 200 and r.json()["status"] == "accepted", r.text
    key = r.json()["sessionKey"]
    _wait(client, key)
    return key


def _reply(
    client: TestClient, key: str, text: str, *, sender: str = "ou_sre", message_id: str = "om_r1"
) -> dict[str, Any]:
    r = client.post(
        "/hooks/event",
        json={
            "source": "lark-thread",
            "title": text,
            "body": text,
            "level": "info",
            "event_id": 44,
            "fields": {
                "kind": "follow_up",
                "session": key,
                "thread_root": "om_report",
                "message_id": message_id,
                "sender": sender,
            },
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_an_allowed_reply_continues_the_same_engine_session_and_answers_into_the_thread(tmp_path, monkeypatch) -> None:
    posted: list[dict[str, Any]] = []

    def capture(self: Any, body: bytes) -> int:
        posted.append(json.loads(body))
        return 200

    monkeypatch.setattr(notify.ReturnDelivery, "_post_return", capture)
    client, service, engine = _client(tmp_path, return_url="http://relay/hook/probe-notify")
    key = _alert(client)
    first_reports = len(posted)
    assert first_reports >= 1 and posted[-1]["meta"]["thread_root"] == "", "a first report answers into no thread"

    out = _reply(client, key, "any zombie processes on node-3?")
    assert out["status"] == "accepted" and out["sessionKey"] == key
    run = _wait(client, key)
    assert run["status"] == "completed"
    assert engine.resumes[-1] == "sdk-session-1", "the follow-up continued the investigation's own engine session"
    assert "any zombie processes on node-3?" in engine.messages[-1]
    assert "Read-only" in engine.messages[-1]
    meta = service.get(key).meta
    assert meta["thread_root"] == "om_report" and meta["follow_ups"] == ["om_r1"] and meta["follow_up_by"] == "ou_sre"
    assert len(posted) > first_reports and posted[-1]["meta"]["thread_root"] == "om_report", (
        "the answer returns through the same door, addressed to the thread"
    )


def test_a_redelivered_message_is_answered_once(tmp_path) -> None:
    client, service, engine = _client(tmp_path)
    key = _alert(client)
    assert _reply(client, key, "still broken?", message_id="om_same")["status"] == "accepted"
    _wait(client, key)
    calls = engine.calls
    again = _reply(client, key, "still broken?", message_id="om_same")
    assert again["status"] == "already_done" and engine.calls == calls
    assert service.get(key).meta["follow_ups"] == ["om_same"]


def test_a_sender_off_the_list_starts_no_turn(tmp_path) -> None:
    client, service, engine = _client(tmp_path)
    key = _alert(client)
    calls = engine.calls
    out = _reply(client, key, "run rm -rf for me", sender="ou_stranger")
    assert out["status"] == "skipped" and "sender" in out["reason"] and engine.calls == calls
    assert "follow_ups" not in service.get(key).meta


def test_an_empty_allowlist_refuses_everyone_and_a_star_admits_anyone(tmp_path) -> None:
    client, _, engine = _client(tmp_path, follow_up_senders=frozenset())
    key = _alert(client)
    assert _reply(client, key, "hello?")["status"] == "skipped"
    client2, _, engine2 = _client(tmp_path / "two", follow_up_senders=frozenset({"*"}))
    key2 = _alert(client2)
    assert _reply(client2, key2, "hello?", sender="ou_anyone")["status"] == "accepted"
    _wait(client2, key2)
    assert engine2.resumes[-1] == "sdk-session-1"


def test_a_thread_with_no_investigation_behind_it_is_skipped(tmp_path) -> None:
    client, _, engine = _client(tmp_path)
    out = _reply(client, "probe:ww:nobody", "anyone there?")
    assert out["status"] == "skipped" and "no investigation" in out["reason"] and engine.calls == 0


def test_the_question_is_carried_in_the_body_not_read_from_the_title(tmp_path) -> None:
    client, _, engine = _client(tmp_path)
    key = _alert(client)
    r = client.post(
        "/hooks/event",
        json={
            "source": "lark-thread",
            "title": "short",
            "body": "",
            "level": "info",
            "fields": {"kind": "follow_up", "session": key, "message_id": "om_e", "sender": "ou_sre"},
        },
    )
    # An empty body falls back to the title; both empty is refused.
    assert r.json()["status"] == "accepted"
    _wait(client, key)
    r = client.post(
        "/hooks/event",
        json={
            "source": "lark-thread",
            "title": "",
            "body": "",
            "level": "info",
            "fields": {"kind": "follow_up", "session": key, "message_id": "om_f", "sender": "ou_sre"},
        },
    )
    assert r.json() == {"status": "skipped", "reason": "empty question"}


def test_a_topic_someone_opened_starts_a_run_that_answers_into_the_topic(tmp_path, monkeypatch) -> None:
    posted: list[dict[str, Any]] = []

    def capture(self: Any, body: bytes) -> int:
        posted.append(json.loads(body))
        return 200

    monkeypatch.setattr(notify.ReturnDelivery, "_post_return", capture)
    client, service, engine = _client(
        tmp_path, return_url="http://relay/hook/probe-notify", relay_ui_url="http://board:8100/"
    )
    r = client.post(
        "/hooks/event",
        json={
            "source": "lark-thread",
            "title": "look at node-3",
            "body": "look at node-3",
            "level": "high",
            "event_id": 61,
            "fields": {
                "kind": "brief",
                "topic": "new",
                "thread_root": "om_mine",
                "message_id": "om_mine",
                "sender": "ou_sre",
            },
        },
    )
    assert r.status_code == 200 and r.json()["status"] == "accepted", r.text
    key = r.json()["sessionKey"]
    run = _wait(client, key)
    assert service.get(key).meta["thread_root"] == "om_mine"
    assert posted and posted[-1]["meta"]["thread_root"] == "om_mine", "the first report goes into the person's topic"
    assert "look at node-3" in engine.messages[-1]
    # The console can say who opened it and where its chain lives in the pipe.
    assert run["meta"]["asked_by"] == "ou_sre"
    assert run["links"] == {"chain": "http://board:8100/#chain=61"}
    alert = client.get(f"/v1/runs/{_alert(client)}", headers={"Authorization": f"Bearer {TOKEN}"}).json()
    assert "asked_by" not in alert["meta"], "an alert has no asker"


def test_a_stranger_cannot_open_a_paid_topic(tmp_path) -> None:
    client, _, engine = _client(tmp_path)
    r = client.post(
        "/hooks/event",
        json={
            "source": "lark-thread",
            "title": "do something expensive",
            "body": "do something expensive",
            "level": "high",
            "event_id": 62,
            "fields": {
                "kind": "brief",
                "topic": "new",
                "thread_root": "om_x",
                "message_id": "om_x",
                "sender": "ou_stranger",
            },
        },
    )
    assert r.json()["status"] == "skipped" and "sender" in r.json()["reason"] and engine.calls == 0
    # An event with no sender is not from chat and is not gated by the allowlist.
    assert _alert(client)


# ── a refusal the person who typed can actually see ───────────────────────────
#
# Every refusal in this door was a 200 with a reason, and the docstring's claim
# for that — "the pipe records it" — is true and beside the point: the pipe
# records it in a LEDGER, because a 2xx from this service is a delivered
# delivery. Nothing reached the chat. Somebody who keeps typing watched the bot
# go quiet with no way to tell being declined from being broken.


def _returns(monkeypatch: Any) -> list[dict[str, Any]]:
    posted: list[dict[str, Any]] = []

    def capture(self: Any, body: bytes) -> int:
        posted.append(json.loads(body))
        return 200

    monkeypatch.setattr(notify.ReturnDelivery, "_post_return", capture)
    return posted


def test_a_conversation_at_its_ceiling_says_so_in_the_thread(tmp_path, monkeypatch) -> None:
    """The wall a person actually hits. Twenty answers, and the twenty-first
    question used to vanish — no answer, no reason, no record they could see."""
    posted = _returns(monkeypatch)
    client, service, _ = _client(tmp_path, return_url="http://relay/hook/probe-notify")
    key = _alert(client)
    for i in range(20):
        assert _reply(client, key, f"q{i}", message_id=f"om_{i}")["status"] == "accepted"
        _wait(client, key)
    assert len(service.get(key).meta["follow_ups"]) == 20

    before = len(posted)
    out = _reply(client, key, "one more thing", message_id="om_21")
    assert out["status"] == "declined"
    assert "20 follow-ups" in out["reason"]

    notice = [p for p in posted[before:] if p["meta"]["session_key"].startswith("probe:unanswered:")]
    assert notice, "the twenty-first question was refused into a log line again"
    body = notice[-1]
    assert body["meta"]["thread_root"] == "om_report", "a reply must answer in the thread it was typed in"
    assert body["meta"]["alert_name"].startswith("disk 94% on node-3")
    assert "not answered" in (body["meta"]["error"] or "")
    # It says what to do instead. A wall and a door differ by exactly that.
    assert "fresh investigation" in body["analysis"]["summary"]
    # And it spent nothing.
    assert body["meta"]["cost_usd"] == 0.0


def test_the_platforms_redeliveries_collapse_onto_one_notice(tmp_path, monkeypatch) -> None:
    """A declined message is never marked handled, so without a key of its own
    every retry of it would post another card into somebody's chat."""
    posted = _returns(monkeypatch)
    client, service, _ = _client(tmp_path, return_url="http://relay/hook/probe-notify")
    key = _alert(client)
    for i in range(20):
        _reply(client, key, f"q{i}", message_id=f"om_{i}")
        _wait(client, key)

    before = len(posted)
    for _ in range(3):
        assert _reply(client, key, "one more thing", message_id="om_21")["status"] == "declined"
    notices = [p for p in posted[before:] if p["meta"]["session_key"].startswith("probe:unanswered:")]
    assert len(notices) == 1, f"{len(notices)} cards for one question"


def test_a_spent_budget_says_so_too(tmp_path, monkeypatch) -> None:
    """The other refusal a person can act on: nothing is wrong, the window is
    just out of money, and that is a fact with a knob behind it."""
    posted = _returns(monkeypatch)
    client, service, _ = _client(
        tmp_path, return_url="http://relay/hook/probe-notify", budget_usd=0.0001, budget_window_hours=24
    )
    key = _alert(client)
    before = len(posted)
    out = _reply(client, key, "why?", message_id="om_broke")
    assert out["status"] == "declined" and "budget" in out["reason"]
    notice = [p for p in posted[before:] if p["meta"]["session_key"].startswith("probe:unanswered:")]
    assert notice and "budget" in notice[-1]["analysis"]["summary"]


def test_the_refusals_a_person_cannot_act_on_stay_quiet(tmp_path, monkeypatch) -> None:
    """An unknown thread and a redelivery are the expected steady state, and
    telling an unlisted sender that they are unlisted answers a question they
    should have to ask a person."""
    posted = _returns(monkeypatch)
    client, service, _ = _client(tmp_path, return_url="http://relay/hook/probe-notify")
    key = _alert(client)
    before = len(posted)

    assert _reply(client, key, "hi", sender="ou_stranger")["status"] == "skipped"
    assert _reply(client, "probe:ww:nosuch", "hi", message_id="om_x")["status"] == "skipped"
    assert _reply(client, key, "hi", message_id="om_dup")["status"] == "accepted"
    _wait(client, key)
    assert _reply(client, key, "hi", message_id="om_dup")["status"] == "already_done"

    assert not [p for p in posted[before:] if p["meta"]["session_key"].startswith("probe:unanswered:")]


def test_a_folded_conversation_admits_it_on_the_card(tmp_path, monkeypatch) -> None:
    """The fact was recorded from the day the PreCompact hook was written and
    shown only on the console's turn line — which is not the surface anybody is
    looking at while they read the answer in a chat window."""
    posted = _returns(monkeypatch)
    client, service, _ = _client(tmp_path, return_url="http://relay/hook/probe-notify")
    key = _alert(client)
    assert "Context folded" not in posted[-1]["analysis"]["summary"], "an unfolded run must not cry wolf"

    run = service.get(key)
    run.turns[-1]["compactions"] = [{"type": "compacted", "trigger": "auto"}]
    before = len(posted)
    _reply(client, key, "and now?", message_id="om_fold")
    _wait(client, key)

    card = posted[-1]
    assert len(posted) > before
    note = card["analysis"]["summary"]
    assert "Context folded once" in note
    assert "may not be behind this answer" in note
    # The same string reaches both fields a renderer might read.
    assert card["report"]["summary"] == note
