"""The sending platform's own id for an alert rides in with the event and out
with the report, so the platform can file the report on that alert's page.

The pipe starts investigations on its own since the platform's investigation
leg was switched off; their reports reached chat and this console, and the
platform's alert page had no way to know they existed.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient

from hookprobe import notify
from hookprobe.app import create_app
from hookprobe.runs import RunStore
from hookprobe.service import RunService
from tests.helpers import FakeEngine, make_settings

TOKEN = "secret-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _client(tmp_path, engine, **overrides):
    settings = make_settings(tmp_path, token=TOKEN, **overrides)
    service = RunService(settings, engine, RunStore(tmp_path / "results"))
    return TestClient(create_app(settings, service)), service


def _returns(monkeypatch: Any) -> list[dict[str, Any]]:
    posted: list[dict[str, Any]] = []

    def capture(self: Any, body: bytes) -> int:
        posted.append(json.loads(body))
        return 200

    monkeypatch.setattr(notify.ReturnDelivery, "_post_return", capture)
    return posted


def test_the_reference_is_kept_on_the_run_and_echoed_on_the_report(tmp_path, monkeypatch):
    posted = _returns(monkeypatch)
    client, service = _client(tmp_path, FakeEngine(), return_url="http://relay/hook/probe-notify")
    with client:
        answer = client.post(
            "/hooks/event",
            json={
                "title": "queue-backlog",
                "body": "ready messages growing",
                "level": "high",
                "source": "judge-notify",
                "event_id": 3301,
                "reference": " 2630 ",
                "fields": {"origin": "platform"},
            },
        ).json()
        assert answer["status"] == "accepted"
        run = service.get(answer["sessionKey"])
        assert run is not None and run.meta["reference"] == "2630", "trimmed, and beside the fields"
        assert "reference" not in (run.meta.get("fields") or {})
    assert posted, "a relay-born run reports back"
    assert posted[-1]["meta"]["reference"] == "2630"
    assert posted[-1]["meta"]["event_id"] == 3301, "the pipe's own id still travels too"
    duration = posted[-1]["meta"]["duration_seconds"]
    assert isinstance(duration, float) and duration >= 0.0, "wall-clock length of the run, for the platform's page"


def test_an_event_without_a_reference_reports_an_empty_one(tmp_path, monkeypatch):
    posted = _returns(monkeypatch)
    client, service = _client(tmp_path, FakeEngine(), return_url="http://relay/hook/probe-notify")
    with client:
        answer = client.post(
            "/hooks/event",
            json={"title": "queue-backlog", "body": "x", "level": "high", "source": "judge-notify", "event_id": 3302},
        ).json()
        assert "reference" not in service.get(answer["sessionKey"]).meta
    assert posted and posted[-1]["meta"]["reference"] == "", "present and empty: the key is part of the dialect"
