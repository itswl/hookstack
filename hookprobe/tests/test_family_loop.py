"""The family loop: the pipe's event door in, the return delivery out."""

import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from fastapi.testclient import TestClient

from hookprobe.app import create_app
from hookprobe.runs import COMPLETED, Run, RunStore
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
    # The processed dialect: what the pipe's renderers actually dress.
    assert payload["meta"]["alert_name"] == "支付网关 5xx · 调查报告"
    assert payload["meta"]["importance"] == "high"
    assert payload["meta"]["status"] == "completed"
    assert payload["analysis"]["summary"] == "ok"
    assert payload["report"]["summary"] == "ok"  # extracted from the JSON answer
    assert verify_timestamped(
        "ret-secret",
        delivery["body"],
        delivery["headers"].get("X-Hook-Signature"),
        delivery["headers"].get("X-Hook-Timestamp"),
    )


# -- restart accounting ------------------------------------------------------


def test_spawn_checkpoints_the_run_to_disk(tmp_path) -> None:
    """The stub is written before the engine starts — that is the crash record."""
    from tests.helpers import GatedEngine

    results = tmp_path / "results"

    async def scenario() -> None:
        engine = GatedEngine()
        service = RunService(make_settings(tmp_path), engine, RunStore(results))
        service.start({"message": "m", "sessionKey": "probe:x:1"})
        for _ in range(100):
            if (results / "probe:x:1.json").exists():
                break
            await asyncio.sleep(0.01)
        data = json.loads((results / "probe:x:1.json").read_text())
        assert data["status"] == "running"
        engine.release.set()
        for _ in range(300):
            run = service.get("probe:x:1")
            if run is not None and run.finished:
                break
            await asyncio.sleep(0.01)
        assert json.loads((results / "probe:x:1.json").read_text())["status"] == "completed"

    asyncio.run(scenario())


def test_restart_sweep_reports_orphans_through_the_loop(tmp_path) -> None:
    _Capture.received = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Capture)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/hook/probe-notify"
    results = tmp_path / "results"

    # What the disk looks like after a crash: a checkpointed run, no process.
    from hookprobe.runs import Run as _Run

    orphan = _Run(session_key="probe:inbound:31", run_id="r1", origin="relay", current_message="investigate")
    orphan.meta = {"title": "支付网关 5xx", "level": "high", "source": "inbound", "event_id": 31}
    RunStore(results).checkpoint(orphan)

    async def next_boot() -> None:
        settings = make_settings(tmp_path, return_url=url, return_secret="ret-secret")
        service = RunService(settings, FakeEngine(), RunStore(results))
        assert service.sweep_orphans() == 1
        run = None
        for _ in range(300):
            run = service.get("probe:inbound:31")
            if run is not None and run.return_status:
                break
            await asyncio.sleep(0.01)
        assert run is not None and run.status == "failed"
        assert run.return_status == "sent"

    try:
        asyncio.run(next_boot())
    finally:
        server.shutdown()

    payload = json.loads(_Capture.received[0]["body"])
    assert payload["meta"]["status"] == "failed"
    assert payload["meta"]["alert_name"] == "支付网关 5xx · 调查报告"
    assert "interrupted by a restart" in payload["report"]["summary"]


def test_app_startup_sweeps_orphans(tmp_path) -> None:
    from hookprobe.runs import Run as _Run

    orphan = _Run(session_key="probe:x:9", run_id="r9", current_message="m")
    RunStore(tmp_path / "results").checkpoint(orphan)
    with make_client(tmp_path, FakeEngine()) as client:
        detail = client.get("/v1/runs/probe:x:9", headers=AUTH).json()
        assert detail["status"] == "failed"
        assert "restart" in detail["error"]


# -- the budget breaker ------------------------------------------------------


def _seed_spend(store: RunStore, cost: float, finished_at: float, key: str) -> None:
    """A finished run whose one turn spent `cost` at `finished_at`."""
    run = Run(session_key=key, run_id="seed", status=COMPLETED, text="t")
    run.turns.append(
        {
            "message": "m",
            "text": "t",
            "error": None,
            "run_id": "seed",
            "cost_usd": cost,
            "finished_at": finished_at,
            "usage": None,
            "model_usage": None,
            "duration_ms": 1,
            "events": [],
        }
    )
    store.create(run)
    store.finish(run)


def _budget_client(tmp_path, engine, store: RunStore, **overrides) -> TestClient:
    settings = make_settings(tmp_path, token=TOKEN, **overrides)
    return TestClient(create_app(settings, RunService(settings, engine, store)))


def test_spend_since_counts_only_the_window(tmp_path) -> None:
    store = RunStore(tmp_path / "results")
    now = time.time()
    _seed_spend(store, 0.75, now, "old:recent")
    _seed_spend(store, 5.0, now - 7200, "old:stale")
    assert store.spend_since(now - 3600) == 0.75
    assert store.spend_since(now - 8000) == 5.75


def test_event_door_refuses_when_budget_exhausted(tmp_path) -> None:
    engine = FakeEngine()
    store = RunStore(tmp_path / "results")
    _seed_spend(store, 1.5, time.time(), "old:funded")
    with _budget_client(tmp_path, engine, store, budget_usd=1.0) as client:
        refused = client.post("/hooks/event", json=EVENT).json()
        assert refused["status"] == "refused"
        assert engine.calls == 0

        detail = client.get(f"/v1/runs/{refused['sessionKey']}", headers=AUTH).json()
        assert detail["status"] == "failed"
        assert "预算熔断" in detail["text"]
        assert detail["meta"]["title"] == EVENT["title"]
        assert detail["cost_usd"] == 0.0

        budget = client.get("/v1/budget", headers=AUTH).json()
        assert budget["exhausted"] is True
        assert budget["spent_usd"] == 1.5
        assert budget["remaining_usd"] == 0.0


def test_event_door_proceeds_under_budget(tmp_path) -> None:
    engine = FakeEngine()
    store = RunStore(tmp_path / "results")
    _seed_spend(store, 1.0, time.time(), "old:funded")
    with _budget_client(tmp_path, engine, store, budget_usd=10.0) as client:
        assert client.post("/hooks/event", json=EVENT).json()["status"] == "accepted"
        for _ in range(100):
            if client.get("/v1/runs/probe:inbound:5", headers=AUTH).json()["status"] != "running":
                break
        assert engine.calls == 1


def test_budget_ignores_spend_outside_window(tmp_path) -> None:
    engine = FakeEngine()
    store = RunStore(tmp_path / "results")
    _seed_spend(store, 5.0, time.time() - 7200, "old:stale")
    with _budget_client(tmp_path, engine, store, budget_usd=1.0, budget_window_hours=1.0) as client:
        assert client.post("/hooks/event", json=EVENT).json()["status"] == "accepted"


def test_operator_paths_ignore_budget(tmp_path) -> None:
    engine = FakeEngine()
    store = RunStore(tmp_path / "results")
    _seed_spend(store, 2.0, time.time(), "old:funded")
    with _budget_client(tmp_path, engine, store, budget_usd=1.0) as client:
        response = client.post("/hooks/agent", json={"message": "manual ask"}, headers=AUTH)
        assert response.status_code == 200
        key = response.json()["sessionKey"]
        for _ in range(100):
            if client.get(f"/v1/runs/{key}", headers=AUTH).json()["status"] != "running":
                break
        assert engine.calls == 1


def test_redelivery_of_funded_session_is_not_refused(tmp_path) -> None:
    engine = FakeEngine()
    store = RunStore(tmp_path / "results")
    with _budget_client(tmp_path, engine, store, budget_usd=1.0) as client:
        first = client.post("/hooks/event", json=EVENT).json()
        assert first["status"] == "accepted"
        for _ in range(100):
            if client.get("/v1/runs/probe:inbound:5", headers=AUTH).json()["status"] != "running":
                break
        # The completed run cost 0.5; push the window over budget, then redeliver.
        _seed_spend(store, 5.0, time.time(), "old:extra")
        again = client.post("/hooks/event", json=EVENT).json()
        assert again["status"] == "accepted"
        assert again["sessionKey"] == first["sessionKey"]
        assert engine.calls == 1


def test_refusal_reports_back_through_the_loop(tmp_path) -> None:
    _Capture.received = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Capture)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/hook/probe-notify"

    store = RunStore(tmp_path / "results")
    _seed_spend(store, 2.0, time.time(), "old:funded")
    try:
        with _budget_client(
            tmp_path, FakeEngine(), store, budget_usd=1.0, return_url=url, return_secret="ret-secret"
        ) as client:
            refused = client.post("/hooks/event", json=EVENT).json()
            assert refused["status"] == "refused"
            for _ in range(300):
                detail = client.get(f"/v1/runs/{refused['sessionKey']}", headers=AUTH).json()
                if detail["return_status"]:
                    break
                time.sleep(0.01)
            assert detail["return_status"] == "sent"
    finally:
        server.shutdown()

    assert len(_Capture.received) == 1
    payload = json.loads(_Capture.received[0]["body"])
    assert payload["meta"]["status"] == "failed"
    assert payload["meta"]["alert_name"] == f"{EVENT['title']} · 调查报告"
    assert "预算熔断" in payload["analysis"]["summary"]
    assert "预算熔断" in payload["report"]["summary"]


def test_failed_return_fires_the_self_alarm(tmp_path) -> None:
    """When the pipe refuses the report, the news travels around it."""
    _Capture.received = []
    alarm_server = ThreadingHTTPServer(("127.0.0.1", 0), _Capture)
    threading.Thread(target=alarm_server.serve_forever, daemon=True).start()
    alarm_url = f"http://127.0.0.1:{alarm_server.server_port}/bot"

    async def scenario() -> None:
        settings = make_settings(
            tmp_path,
            return_url="http://127.0.0.1:9/hook/probe-notify",  # nothing listens here
            alarm_url=alarm_url,
        )
        service = RunService(settings, FakeEngine(), RunStore(tmp_path / "results"))
        service._return_delays = (0.0, 0.0, 0.0)  # collapse the backoff for the test
        service.start(
            {"message": "investigate", "sessionKey": "probe:inbound:41", "_meta": {"title": "T", "level": "high"}},
            origin="relay",
        )
        run = None
        for _ in range(300):
            run = service.get("probe:inbound:41")
            if run is not None and run.return_status:
                break
            await asyncio.sleep(0.01)
        assert run is not None and run.return_status.startswith("failed")
        for _ in range(100):
            if _Capture.received:
                break
            await asyncio.sleep(0.01)
        assert service.return_failure_count() == 1

    try:
        asyncio.run(scenario())
    finally:
        alarm_server.shutdown()

    assert len(_Capture.received) == 1
    alarm = json.loads(_Capture.received[0]["body"])
    assert "调查报告回传失败" in alarm["content"]["text"]
    assert "probe:inbound:41" in alarm["content"]["text"]


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
