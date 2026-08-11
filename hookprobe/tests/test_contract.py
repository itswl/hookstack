"""The contract WebhookWise's OpenClaw client actually exercises.

Payload shapes mirror services/analysis/openclaw_analysis.py (trigger) and
services/analysis/openclaw_client.py (poll) in WebhookWise: change these
tests only together with a WebhookWise-side change.
"""

import json
import time

from fastapi.testclient import TestClient

from hookprobe.app import create_app
from hookprobe.runs import RunStore
from hookprobe.service import RunService
from tests.helpers import FakeEngine, GatedEngine, make_settings

TOKEN = "secret-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

# What analyze_with_openclaw() actually POSTs.
WW_TRIGGER_PAYLOAD = {
    "message": "...deep analysis prompt + alert payload...",
    "name": "deep-analysis",
    "sessionKey": "hook:deep-analysis:grafana:abc-123",
    "wakeMode": "now",
    "deliver": False,
    "thinking": "high",
    "timeoutSeconds": 900,
}


def make_client(tmp_path, engine) -> TestClient:
    settings = make_settings(tmp_path, token=TOKEN)
    service = RunService(settings, engine, RunStore(tmp_path / "results"))
    return TestClient(create_app(settings, service))


def poll_until_final(client: TestClient, session_key: str, deadline: float = 3.0):
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        response = client.get(f"/sessions/{session_key}/final", headers=AUTH)
        if response.status_code == 200:
            return response
        assert response.status_code == 202, response.text
        time.sleep(0.02)
    raise AssertionError("final result never arrived")


def test_healthz_is_open(tmp_path) -> None:
    with make_client(tmp_path, FakeEngine()) as client:
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


def test_auth_is_required_on_contract_routes(tmp_path) -> None:
    with make_client(tmp_path, FakeEngine()) as client:
        assert client.post("/hooks/agent", json=WW_TRIGGER_PAYLOAD).status_code == 401
        bad = {"Authorization": "Bearer wrong"}
        assert client.post("/hooks/agent", json=WW_TRIGGER_PAYLOAD, headers=bad).status_code == 401
        assert client.get("/sessions/x/final").status_code == 401
        assert client.get("/v1/runs/x", headers=bad).status_code == 401


def test_trigger_then_poll_roundtrip(tmp_path) -> None:
    engine = GatedEngine()
    with make_client(tmp_path, engine) as client:
        trigger = client.post("/hooks/agent", json=WW_TRIGGER_PAYLOAD, headers=AUTH)
        assert trigger.status_code == 200
        body = trigger.json()
        assert body["runId"]
        assert body["sessionKey"] == WW_TRIGGER_PAYLOAD["sessionKey"]

        # Still running: WebhookWise treats 202 as "analysis in progress".
        pending = client.get(f"/sessions/{body['sessionKey']}/final", headers=AUTH)
        assert pending.status_code == 202

        engine.release.set()
        final = poll_until_final(client, body["sessionKey"])
        payload = final.json()
        assert payload["isFinal"] is True
        assert payload["text"] == '{"summary": "done"}'
        assert payload["messageCount"] == 2


def test_retrigger_returns_same_run(tmp_path) -> None:
    engine = GatedEngine()
    with make_client(tmp_path, engine) as client:
        first = client.post("/hooks/agent", json=WW_TRIGGER_PAYLOAD, headers=AUTH).json()
        second = client.post("/hooks/agent", json=WW_TRIGGER_PAYLOAD, headers=AUTH).json()
        assert first["runId"] == second["runId"]
        engine.release.set()
        poll_until_final(client, first["sessionKey"])


def test_unknown_session_is_404(tmp_path) -> None:
    with make_client(tmp_path, FakeEngine()) as client:
        response = client.get("/sessions/hook:never-seen/final", headers=AUTH)
        assert response.status_code == 404


def test_empty_message_is_400(tmp_path) -> None:
    with make_client(tmp_path, FakeEngine()) as client:
        response = client.post("/hooks/agent", json={"sessionKey": "hook:x"}, headers=AUTH)
        assert response.status_code == 400


def test_continue_endpoint_roundtrip(tmp_path) -> None:
    with make_client(tmp_path, FakeEngine()) as client:
        body = client.post("/hooks/agent", json=WW_TRIGGER_PAYLOAD, headers=AUTH).json()
        key = body["sessionKey"]
        poll_until_final(client, key)

        # Guards: busy sessions conflict, unknown sessions 404, auth required.
        assert client.post(f"/sessions/{key}/continue", json={"message": "x"}).status_code == 401
        assert client.post("/sessions/hook:nope/continue", json={"message": "x"}, headers=AUTH).status_code == 404
        assert client.post(f"/sessions/{key}/continue", json={"message": ""}, headers=AUTH).status_code == 400

        followup = client.post(f"/sessions/{key}/continue", json={"message": "check node capacity"}, headers=AUTH)
        assert followup.status_code == 200
        assert followup.json()["sessionKey"] == key

        final = poll_until_final(client, key)
        assert final.json()["isFinal"] is True

        detail = client.get(f"/v1/runs/{key}", headers=AUTH).json()
        assert detail["engine_session_id"] == "sdk-session-1"
        assert len(detail["turns"]) == 2
        assert detail["turns"][1]["message"] == "check node capacity"


def test_run_list_and_ui_page(tmp_path) -> None:
    with make_client(tmp_path, FakeEngine()) as client:
        # The list needs auth; the page markup does not (it holds no data).
        assert client.get("/v1/runs").status_code == 401
        page = client.get("/ui")
        assert page.status_code == 200
        assert "hookprobe" in page.text
        assert client.get("/", follow_redirects=False).status_code in (302, 307)

        client.post("/hooks/agent", json=WW_TRIGGER_PAYLOAD, headers=AUTH)
        second = dict(WW_TRIGGER_PAYLOAD, sessionKey="web:manual-1", message="check disk usage on node-3")
        client.post("/hooks/agent", json=second, headers=AUTH)
        poll_until_final(client, WW_TRIGGER_PAYLOAD["sessionKey"])
        poll_until_final(client, "web:manual-1")

        runs = client.get("/v1/runs", headers=AUTH).json()
        assert {r["session_key"] for r in runs} == {WW_TRIGGER_PAYLOAD["sessionKey"], "web:manual-1"}
        manual = next(r for r in runs if r["session_key"] == "web:manual-1")
        assert manual["status"] == "completed"
        assert manual["turn_count"] == 1
        assert manual["title"].startswith("check disk usage")


def test_run_list_includes_persisted_runs_after_restart(tmp_path) -> None:
    with make_client(tmp_path, FakeEngine()) as client:
        client.post("/hooks/agent", json=WW_TRIGGER_PAYLOAD, headers=AUTH)
        poll_until_final(client, WW_TRIGGER_PAYLOAD["sessionKey"])

    # New app over the same results dir — the finished run must reappear.
    with make_client(tmp_path, FakeEngine()) as reborn:
        runs = reborn.get("/v1/runs", headers=AUTH).json()
        assert [r["session_key"] for r in runs] == [WW_TRIGGER_PAYLOAD["sessionKey"]]
        detail = reborn.get(f"/v1/runs/{WW_TRIGGER_PAYLOAD['sessionKey']}", headers=AUTH).json()
        assert len(detail["turns"]) == 1


def test_continue_while_running_is_409(tmp_path) -> None:
    engine = GatedEngine()
    with make_client(tmp_path, engine) as client:
        body = client.post("/hooks/agent", json=WW_TRIGGER_PAYLOAD, headers=AUTH).json()
        conflict = client.post(f"/sessions/{body['sessionKey']}/continue", json={"message": "x"}, headers=AUTH)
        assert conflict.status_code == 409
        engine.release.set()
        poll_until_final(client, body["sessionKey"])


def test_engine_failure_is_served_as_final_report(tmp_path) -> None:
    with make_client(tmp_path, FakeEngine(exc=RuntimeError("engine exploded"))) as client:
        body = client.post("/hooks/agent", json=WW_TRIGGER_PAYLOAD, headers=AUTH).json()
        final = poll_until_final(client, body["sessionKey"])
        payload = final.json()
        assert payload["isFinal"] is True
        report = json.loads(payload["text"])
        assert "hookprobe run failed" in report["summary"]

        detail = client.get(f"/v1/runs/{body['sessionKey']}", headers=AUTH).json()
        assert detail["status"] == "failed"
        assert "engine exploded" in detail["error"]
