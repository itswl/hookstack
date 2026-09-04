"""The family loop: the pipe's event door in, the return delivery out."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from fastapi.testclient import TestClient

from hookprobe.app import create_app
from hookprobe.engine import EngineResult
from hookprobe.runs import COMPLETED, Run, RunStore
from hookprobe.service import RunService
from hookprobe.wire import sign_timestamped, verify_timestamped
from tests.helpers import FakeEngine, GatedEngine, make_settings

TOKEN = "secret-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

EVENT = {
    "title": "Payment gateway 5xx rate 8.1%",
    "body": "gateway-2 5xx at 8.1% over the last 5 minutes",
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
        assert "Payment gateway" in engine.messages[0]
        assert detail["origin"] == "relay"
        assert detail["meta"]["title"] == EVENT["title"]


def test_a_work_item_is_investigated_as_work_not_as_an_incident(tmp_path) -> None:
    """`fields.kind: task` asks a different question of the same door.

    The watcher forwards work signals — someone assigned something, someone
    asked — and "find the root cause" is the wrong instruction for those:
    nothing is broken. The door keeps one contract and swaps the question.
    """
    engine = FakeEngine()
    with make_client(tmp_path, engine) as client:
        task = dict(
            EVENT,
            event_id=77,
            title="Redis monitoring: slow-command rate and key-count growth",
            fields={"kind": "task", "origin": "the chat server"},
        )
        assert client.post("/hooks/event", json=task).json()["status"] == "accepted"
        for _ in range(100):
            detail = client.get("/v1/runs/probe:inbound:77", headers=AUTH).json()
            if detail["status"] != "running":
                break

    prompt = engine.messages[0]
    assert "how would this be done" in prompt
    assert "NOT an alert" in prompt
    # Assert the INSTRUCTION is gone, not the words: the task prompt says
    # "no root cause to find", which is the point, so a substring check on
    # "root cause" would pass for the wrong reason and fail for the right one.
    assert "find the root cause" not in prompt
    assert "no root cause to find" in prompt
    # And it must not invite a remediation block, which is what feeds the
    # approval path — this door answers how, the person does it.
    assert "```remediation" not in prompt
    # The ceiling is stated rather than hidden: it cannot read repositories, so
    # it is told to name the gap instead of guessing at one.
    assert "unknowns" in prompt


def test_the_prompt_asks_for_a_verdict_only_where_one_was_declared(tmp_path) -> None:
    """A prompt change is a golden-replay event in this family, so a deployment
    that has not opted in must keep a byte-identical prompt."""
    silent = FakeEngine()
    with make_client(tmp_path / "off", silent) as client:
        assert client.post("/hooks/event", json=EVENT).json()["status"] == "accepted"
        _drain(client, "probe:inbound:5")
    assert "VERDICT" not in silent.messages[0]

    asking = FakeEngine()
    with make_client(tmp_path / "on", asking, verdicts=frozenset({"needs_plan", "informational"})) as client:
        assert client.post("/hooks/event", json=EVENT).json()["status"] == "accepted"
        _drain(client, "probe:inbound:5")
    prompt = asking.messages[0]
    assert "VERDICT: <one of: informational | needs_plan>" in prompt, (
        "the options are spelled out so a run is not wasted guessing at a vocabulary it cannot see"
    )
    assert prompt.startswith(silent.messages[0]), "the instruction is appended, never woven into the question"


def test_a_brief_is_run_as_written_instead_of_wrapped(tmp_path) -> None:
    """The third question, and the one that is not a question.

    Both other prompts presume a SUBJECT to analyse and wrap the body in that
    framing. A scheduled procedure has neither shape, and the framing fights it:
    the first watcher round did `date` (from the brief), grepped case files (from
    the alert wrapper), called no MCP tool at all, and answered with the brief's
    own silence token. A blend of two instructions is what wrapping a procedure
    produces.
    """
    engine = FakeEngine()
    brief = "Run `date` first. If outside 09:30-19:30, answer [SILENT].\nOtherwise list the feeds."
    with make_client(tmp_path, engine) as client:
        due = dict(EVENT, event_id=88, title="Watch round", body=brief, fields={"kind": "brief", "env": "work"})
        assert client.post("/hooks/event", json=due).json()["status"] == "accepted"
        _drain(client, "probe:inbound:88")

    prompt = engine.messages[0]
    assert prompt.startswith(brief), "the brief is the prompt, not an item inside one"
    # None of the framing either other door adds may appear.
    assert "find the root cause" not in prompt
    assert "how would this be done" not in prompt
    assert "Open the case files first" not in prompt
    assert "MEMORY-SUGGESTION" not in prompt
    # The trigger's own context still travels, marked as what it is.
    assert "not instructions" in prompt and "work" in prompt


def test_a_brief_gets_room_a_procedure_needs(tmp_path) -> None:
    """Truncating an alert loses detail about one incident; truncating a
    procedure deletes STEPS from it, and the run then does most of a job and
    reports success. The first real brief landed at 3,799 bytes against the
    4,000 cap — two more paragraphs from a failure nothing would have reported."""
    engine = FakeEngine()
    long_brief = "step one.\n" + ("padding line\n" * 900) + "FINAL STEP: post the signal."
    assert len(long_brief) > 4000
    with make_client(tmp_path, engine) as client:
        due = dict(EVENT, event_id=89, title="Long round", body=long_brief, fields={"kind": "brief"})
        assert client.post("/hooks/event", json=due).json()["status"] == "accepted"
        _drain(client, "probe:inbound:89")
    assert "FINAL STEP: post the signal." in engine.messages[0], "the last step survived the cap"

    # An ALERT body keeps the tighter cap: it arrives from an upstream nobody
    # here controls, which is the whole reason that cap exists.
    alert_engine = FakeEngine()
    with make_client(tmp_path / "alert", alert_engine) as client:
        fat = dict(EVENT, event_id=90, body=long_brief)
        assert client.post("/hooks/event", json=fat).json()["status"] == "accepted"
        _drain(client, "probe:inbound:90")
    assert "FINAL STEP: post the signal." not in alert_engine.messages[0]


def test_an_event_without_a_kind_is_still_investigated_as_an_alert(tmp_path) -> None:
    engine = FakeEngine()
    with make_client(tmp_path, engine) as client:
        assert client.post("/hooks/event", json=EVENT).json()["status"] == "accepted"
        for _ in range(100):
            detail = client.get("/v1/runs/probe:inbound:5", headers=AUTH).json()
            if detail["status"] != "running":
                break
    assert "root cause" in engine.messages[0]


def test_event_door_requires_signature_when_secret_set(tmp_path) -> None:
    with make_client(tmp_path, FakeEngine(), event_secret="pipe-secret") as client:
        body = json.dumps(EVENT).encode()
        assert client.post("/hooks/event", content=body).status_code == 401

        headers = {"Content-Type": "application/json", **sign_timestamped("pipe-secret", body)}
        response = client.post("/hooks/event", content=body, headers=headers)
        assert response.status_code == 200
        assert response.json()["status"] == "accepted"


def test_the_event_door_bounds_what_reaches_the_prompt(tmp_path) -> None:
    """Only `body` was ever capped. `title`, `source` and `fields` arrive from an
    upstream payload nobody in this family controls, and `fields` is a json.dumps
    of an arbitrary object — so a 5 MB one reached the model verbatim, on the
    token bill of every turn of that investigation and in its case file for as
    long as the volume keeps it."""
    engine = FakeEngine()
    with make_client(tmp_path, engine) as client:
        fat = dict(
            EVENT,
            event_id=11,
            title="T" * 5000,
            source="S" * 5000,
            body="B" * 50000,
            fields={f"key-{i}": "v" * 200 for i in range(200)},
        )
        accepted = client.post("/hooks/event", json=fat).json()
        assert accepted["status"] == "accepted"
        # The key names the case file and every audit line for this session.
        assert accepted["sessionKey"] == "probe:" + "S" * 120 + ":11"
        for _ in range(300):
            if engine.messages:
                break
            time.sleep(0.01)

        prompt = engine.messages[0]
        assert len(prompt) < 12000, f"the prompt was {len(prompt)} characters"
        assert "T" * 301 not in prompt and "S" * 121 not in prompt and "B" * 4001 not in prompt
        assert "truncated" in prompt, "the fence says where the object went"
        _drain(client, accepted["sessionKey"])

        # A body nobody could have meant is refused before it is parsed.
        oversize = client.post("/hooks/event", content=b'{"body": "' + b"y" * 200_000 + b'"}')
        assert oversize.status_code == 413
        assert engine.calls == 1


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
                "_meta": {"title": "Payment gateway 5xx", "level": "high", "source": "inbound", "event_id": 9},
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
    assert payload["meta"]["alert_name"] == "Payment gateway 5xx · investigation"
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


def test_a_declared_verdict_reaches_the_pipe_and_an_undeclared_one_does_not(tmp_path) -> None:
    """The chain's middle.

    `meta.importance` is the level of the event that came IN, so it says nothing
    about what the investigation found — this is the only field on the return
    trip a downstream route can key on to learn the investigator's own
    conclusion. The second half of the assertion is the point of the closed
    vocabulary: a label nobody declared leaves no verdict rather than a lane.
    """
    _Capture.received = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Capture)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/hook/probe-notify"

    async def scenario(answer: str, key: str) -> None:
        settings = make_settings(
            tmp_path / key,
            return_url=url,
            return_secret="ret-secret",
            verdicts=frozenset({"needs_plan"}),
        )
        service = RunService(
            settings,
            FakeEngine(result=EngineResult(text=answer, message_count=3, cost_usd=0.5)),
            RunStore(tmp_path / key / "results"),
        )
        service.start(
            {
                "message": "investigate",
                "sessionKey": f"probe:inbound:{key}",
                "_meta": {"title": "Deploy blocked", "level": "high", "source": "inbound", "event_id": key},
            },
            origin="relay",
        )
        for _ in range(300):
            run = service.get(f"probe:inbound:{key}")
            if run is not None and run.return_status:
                break
            await asyncio.sleep(0.01)
        assert run is not None and run.return_status == "sent"

    try:
        asyncio.run(scenario('{"summary": "ok"}\nVERDICT: needs_plan', "declared"))
        asyncio.run(scenario('{"summary": "ok"}\nVERDICT: buy_a_server', "undeclared"))
    finally:
        server.shutdown()

    declared, undeclared = (json.loads(item["body"]) for item in _Capture.received)
    assert declared["meta"]["verdict"] == "needs_plan"
    assert undeclared["meta"]["verdict"] == ""
    assert declared["meta"]["importance"] == undeclared["meta"]["importance"] == "high", (
        "importance echoes the incoming level in both — which is exactly why the verdict field had to exist"
    )


def test_a_patrol_asks_for_its_report_and_a_polled_run_does_not(tmp_path) -> None:
    """Three patrol runs cost $2.20 on production and were delivered nowhere.

    The guard was `origin != "relay"`, correct for the two callers that existed:
    a relay-born run reports back, a platform-born one is POLLED at /final and
    returning as well would deliver it twice. A patrol is a third case with
    neither — the crontab that fired it is long gone — so its report reached a
    JSON file on the volume and stopped there.

    Both halves matter. Returning everything would double-deliver every
    investigation the platform starts, which is the reason the guard exists.
    """
    _Capture.received = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Capture)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/hook/probe-notify"

    async def scenario() -> None:
        settings = make_settings(tmp_path, return_url=url, return_secret="ret-secret")
        service = RunService(settings, FakeEngine(), RunStore(tmp_path / "results"))

        # Posted straight to /hooks/agent, so origin is empty either way.
        service.start(
            {
                "message": "Patrol: self review",
                "sessionKey": "patrol:self-review:2026-08-20",
                "_meta": {"patrol": "self-review", "title": "Patrol: self review", "notify": True},
            }
        )
        service.start({"message": "investigate", "sessionKey": "platform:polled:1"})

        for _ in range(300):
            patrol = service.get("patrol:self-review:2026-08-20")
            if patrol is not None and patrol.return_status:
                break
            await asyncio.sleep(0.01)

        polled = service.get("platform:polled:1")
        assert patrol is not None and patrol.return_status == "sent"
        assert polled is not None and polled.finished, "the polled run still ran to completion"
        assert not polled.return_status, "nothing polls a patrol; the platform polls this one"

    try:
        asyncio.run(scenario())
    finally:
        server.shutdown()

    assert len(_Capture.received) == 1, "one delivery, not two"
    payload = json.loads(_Capture.received[0]["body"])
    assert payload["meta"]["alert_name"] == "Patrol: self review · investigation"
    assert payload["meta"]["session_key"] == "patrol:self-review:2026-08-20"


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
    orphan.meta = {"title": "Payment gateway 5xx", "level": "high", "source": "inbound", "event_id": 31}
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
    assert payload["meta"]["alert_name"] == "Payment gateway 5xx · investigation"
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
        assert "Budget breaker open" in detail["text"]
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
    assert payload["meta"]["alert_name"] == f"{EVENT['title']} · investigation"
    assert "Budget breaker open" in payload["analysis"]["summary"]
    assert "Budget breaker open" in payload["report"]["summary"]


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
    assert "report return failed" in alarm["content"]["text"]
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


# --- storm coalescing: one condition, one session -----------------------------


def _drain(client: TestClient, key: str) -> dict:
    for _ in range(300):
        detail = client.get(f"/v1/runs/{key}", headers=AUTH).json()
        if detail["status"] != "running":
            return detail
        time.sleep(0.01)
    raise AssertionError("run never finished")


def test_a_refire_joins_the_finished_investigation(tmp_path) -> None:
    """Same source+title, new event id, inside the window: a follow-up turn in
    the session that already mapped the condition — not a second cold start."""
    engine = FakeEngine()
    with make_client(tmp_path, engine) as client:
        first = client.post("/hooks/event", json=EVENT).json()
        _drain(client, first["sessionKey"])

        refire = client.post("/hooks/event", json=dict(EVENT, event_id=6, level="critical")).json()

        assert refire["status"] == "coalesced"
        assert refire["sessionKey"] == first["sessionKey"]
        detail = _drain(client, first["sessionKey"])
        assert engine.calls == 2
        # The point of continuing rather than restarting: the engine session.
        assert engine.resumes[1] == "sdk-session-1"
        assert "fired again" in engine.messages[1]
        assert "critical" in engine.messages[1], "the refire carries the NEW severity"
        assert len(detail["turns"]) == 2
        assert detail["meta"]["refires"] == 1
        assert detail["meta"]["level"] == "critical"


def test_a_refire_while_running_spends_nothing(tmp_path) -> None:
    """A live session already claims its alert; a re-fire adds no question."""
    engine = GatedEngine()
    with make_client(tmp_path, engine) as client:
        first = client.post("/hooks/event", json=EVENT).json()

        refire = client.post("/hooks/event", json=dict(EVENT, event_id=6)).json()

        assert refire == {
            "status": "coalesced",
            "state": "investigating",
            "sessionKey": first["sessionKey"],
            "runId": first["runId"],
        }
        assert len(engine.resumes) == 1
        engine.release.set()
        _drain(client, first["sessionKey"])


def test_redelivery_of_the_same_event_id_is_still_idempotent(tmp_path) -> None:
    """A retry of the SAME event is delivery plumbing, not a re-fire; it must
    not buy a follow-up turn."""
    engine = FakeEngine()
    with make_client(tmp_path, engine) as client:
        first = client.post("/hooks/event", json=EVENT).json()
        _drain(client, first["sessionKey"])

        again = client.post("/hooks/event", json=EVENT).json()

        assert again["status"] == "accepted"
        assert again["sessionKey"] == first["sessionKey"]
        assert engine.calls == 1


def test_a_different_alert_is_never_coalesced(tmp_path) -> None:
    engine = FakeEngine()
    with make_client(tmp_path, engine) as client:
        first = client.post("/hooks/event", json=EVENT).json()
        _drain(client, first["sessionKey"])

        other = client.post("/hooks/event", json=dict(EVENT, event_id=7, title="Disk full on db-1")).json()

        assert other["status"] == "accepted"
        assert other["sessionKey"] != first["sessionKey"]
        _drain(client, other["sessionKey"])
        assert engine.calls == 2
        assert engine.resumes[1] is None


def test_coalescing_off_means_every_fire_is_its_own_run(tmp_path) -> None:
    engine = FakeEngine()
    with make_client(tmp_path, engine, coalesce_window_seconds=0) as client:
        first = client.post("/hooks/event", json=EVENT).json()
        _drain(client, first["sessionKey"])

        refire = client.post("/hooks/event", json=dict(EVENT, event_id=6)).json()

        assert refire["status"] == "accepted"
        assert refire["sessionKey"] != first["sessionKey"]
        _drain(client, refire["sessionKey"])
        assert engine.resumes == [None, None]


def test_a_refire_outside_the_window_starts_fresh(tmp_path) -> None:
    engine = FakeEngine()
    with make_client(tmp_path, engine, coalesce_window_seconds=1) as client:
        first = client.post("/hooks/event", json=EVENT).json()
        _drain(client, first["sessionKey"])

        time.sleep(1.1)
        refire = client.post("/hooks/event", json=dict(EVENT, event_id=6)).json()

        assert refire["status"] == "accepted"
        assert refire["sessionKey"] != first["sessionKey"]
        _drain(client, refire["sessionKey"])


def test_a_refire_with_the_budget_gone_stands_on_the_delivered_report(tmp_path) -> None:
    """A follow-up spends money too — but unlike a refused cold start, the
    original report already reached the channels, so standing is not a drop."""
    engine = FakeEngine()
    store = RunStore(tmp_path / "results")
    with _budget_client(tmp_path, engine, store, budget_usd=10.0) as client:
        first = client.post("/hooks/event", json=EVENT).json()
        _drain(client, first["sessionKey"])

        _seed_spend(store, 100.0, time.time(), "old:funded")
        refire = client.post("/hooks/event", json=dict(EVENT, event_id=6)).json()

        assert refire["status"] == "skipped"
        assert "previous report stands" in refire["reason"]
        assert refire["sessionKey"] == first["sessionKey"]
        assert engine.calls == 1


def test_agent_door_refuses_over_budget_only_when_told_to(tmp_path) -> None:
    """The breaker's docstring says it guards 'the only path that spends money
    without a human asking' — and on the deployment this flag was written for,
    /hooks/agent IS such a path: a platform fires it from webhooks, $7 a day,
    ungated. Off by default (a person's explicit trigger should not bounce off
    a meter); the flag says which kind of caller a deployment has."""
    trigger = {"message": "investigate", "sessionKey": "hook:x:1"}

    # Flag off: exhausted budget, the door still starts the run.
    engine = FakeEngine()
    store = RunStore(tmp_path / "off" / "results")
    _seed_spend(store, 1.5, time.time(), "old:funded")
    with _budget_client(tmp_path / "off", engine, store, budget_usd=1.0) as client:
        assert "runId" in client.post("/hooks/agent", json=trigger, headers=AUTH).json()
        deadline = time.monotonic() + 3.0
        while engine.calls == 0 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert engine.calls == 1

    # Flag on: refused as a report-shaped run, engine never called.
    engine = FakeEngine()
    store = RunStore(tmp_path / "on" / "results")
    _seed_spend(store, 1.5, time.time(), "old:funded")
    with _budget_client(tmp_path / "on", engine, store, budget_usd=1.0, budget_gates_agent_door=True) as client:
        refused = client.post("/hooks/agent", json=trigger, headers=AUTH).json()
        assert refused["status"] == "refused"
        assert engine.calls == 0
        detail = client.get(f"/v1/runs/{refused['sessionKey']}", headers=AUTH).json()
        assert "Budget breaker open" in detail["text"] and detail["cost_usd"] == 0.0

        # An already-funded session stays reachable through the same door.
        assert (
            client.post("/hooks/agent", json={"message": "m", "sessionKey": "old:funded"}, headers=AUTH).json()[
                "sessionKey"
            ]
            == "old:funded"
        )
        assert engine.calls == 0, "idempotent return of an existing run, not a new spend"
