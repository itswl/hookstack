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
