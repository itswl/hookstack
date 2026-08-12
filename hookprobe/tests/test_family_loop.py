"""The family loop: the pipe's event door in, the return delivery out."""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from fastapi.testclient import TestClient

from hookprobe.app import create_app
from hookprobe.runs import RunStore
from hookprobe.service import RunService
from hookprobe.wire import sign_timestamped, verify_timestamped
from tests.helpers import FakeEngine, make_settings

TOKEN = "secret-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

EVENT = {
    "title": "支付网关 5xx 比例 8.1%",
    "body": "gateway-2 近 5 分钟 5xx 8.1%",
    "level": "high",
    "source": "inbound",
    "event_id": 5,
    "fields": {"env": "prod"},
}


def make_client(tmp_path, engine, **overrides) -> TestClient:
    settings = make_settings(tmp_path, token=TOKEN, **overrides)
    service = RunService(settings, engine, RunStore(tmp_path / "results"))
    return TestClient(create_app(settings, service))


def test_wire_signature_roundtrip() -> None:
    body = b'{"a":1}'
    headers = sign_timestamped("s3cret", body)
    assert verify_timestamped("s3cret", body, headers["X-Hook-Signature"], headers["X-Hook-Timestamp"])
    assert not verify_timestamped("s3cret", b'{"a":2}', headers["X-Hook-Signature"], headers["X-Hook-Timestamp"])
    assert not verify_timestamped("s3cret", body, headers["X-Hook-Signature"], "1000000")  # stale
    assert verify_timestamped("", body, None, None)  # unsigned door accepts
    assert sign_timestamped("", body) == {}


def test_event_door_escalates_and_skips(tmp_path) -> None:
    engine = FakeEngine()
    with make_client(tmp_path, engine) as client:
        low = dict(EVENT, level="info", event_id=1)
        response = client.post("/hooks/event", json=low)
        assert response.status_code == 200
        assert response.json()["status"] == "skipped"
        assert engine.calls == 0

        accepted = client.post("/hooks/event", json=EVENT).json()
        assert accepted["status"] == "accepted"
        assert accepted["sessionKey"] == "probe:inbound:5"

        # A restatement storm funds one investigation, not N.
        again = client.post("/hooks/event", json=EVENT).json()
        assert again["sessionKey"] == accepted["sessionKey"]

        assert client.post("/hooks/event", json=dict(EVENT, title="")).status_code == 400

        # give the run a beat to finish, then check what the engine was told
        for _ in range(100):
            detail = client.get("/v1/runs/probe:inbound:5", headers=AUTH).json()
            if detail["status"] != "running":
                break
        assert engine.calls == 1
        assert "支付网关" in engine.messages[0]
        assert detail["origin"] == "relay"
        assert detail["meta"]["title"] == EVENT["title"]


def test_event_door_requires_signature_when_secret_set(tmp_path) -> None:
    with make_client(tmp_path, FakeEngine(), event_secret="pipe-secret") as client:
        body = json.dumps(EVENT).encode()
        assert client.post("/hooks/event", content=body).status_code == 401

        headers = {"Content-Type": "application/json", **sign_timestamped("pipe-secret", body)}
        response = client.post("/hooks/event", content=body, headers=headers)
        assert response.status_code == 200
        assert response.json()["status"] == "accepted"


class _Capture(BaseHTTPRequestHandler):
    received: list[dict] = []

    def do_POST(self):  # noqa: N802 — http.server API
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        type(self).received.append({"body": body, "headers": dict(self.headers)})
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):  # noqa: D102
        pass


def test_relay_born_runs_report_back(tmp_path) -> None:
    _Capture.received = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Capture)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/hook/probe-notify"

    async def scenario() -> None:
        settings = make_settings(tmp_path, return_url=url, return_secret="ret-secret")
        service = RunService(settings, FakeEngine(), RunStore(tmp_path / "results"))
        service.start(
            {
                "message": "investigate",
                "sessionKey": "probe:inbound:9",
                "_meta": {"title": "支付网关 5xx", "level": "high", "source": "inbound", "event_id": 9},
            },
            origin="relay",
        )
        for _ in range(300):
            run = service.get("probe:inbound:9")
            if run is not None and run.return_status:
                break
            await asyncio.sleep(0.01)
        assert run is not None and run.return_status == "sent"

    try:
        asyncio.run(scenario())
    finally:
        server.shutdown()

    assert len(_Capture.received) == 1
    delivery = _Capture.received[0]
    payload = json.loads(delivery["body"])
    assert payload["meta"]["title"] == "支付网关 5xx"
    assert payload["meta"]["status"] == "completed"
    assert payload["report"]["summary"] == "ok"  # extracted from the JSON answer
    assert verify_timestamped(
        "ret-secret",
        delivery["body"],
        delivery["headers"].get("X-Hook-Signature"),
        delivery["headers"].get("X-Hook-Timestamp"),
    )


def test_api_born_runs_do_not_report_back(tmp_path) -> None:
    _Capture.received = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Capture)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/hook/probe-notify"

    async def scenario() -> None:
        settings = make_settings(tmp_path, return_url=url)
        service = RunService(settings, FakeEngine(), RunStore(tmp_path / "results"))
        service.start({"message": "analyze", "sessionKey": "hook:normal"})
        for _ in range(200):
            run = service.get("hook:normal")
            if run is not None and run.finished:
                break
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.1)  # a callback would have fired by now

    try:
        asyncio.run(scenario())
    finally:
        server.shutdown()
    assert _Capture.received == []
