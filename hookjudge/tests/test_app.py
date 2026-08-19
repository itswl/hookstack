"""The service end to end: accept fast, judge behind, hand the result back."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from dataclasses import replace
from typing import Any

import httpx
import pytest

from hookjudge.app import create_app
from hookjudge.settings import Settings

EVENT = {
    "meta": {"source": "grafana", "correlation_id": "hr-86"},
    "event": {
        "title": "Single top-up over 500",
        "body": "account 42 topped up 920",
        "level": "high",
        "fields": {"project": "demo-alarm", "env": "prod"},
    },
    "raw": {"state": "alerting"},
}


class Sink:
    """The pipe's return door."""

    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.received: list[dict[str, Any]] = []

    async def post(self, url: str, **kwargs: Any) -> Any:
        body = kwargs.get("content") or b"{}"
        self.received.append({"url": url, "headers": kwargs.get("headers") or {}, "body": json.loads(body.decode())})

        class _R:
            status_code = self.status
            text = "{}"

        return _R()


def _settings(tmp_path, **overrides: Any) -> Settings:
    return replace(
        Settings(
            db_path=str(tmp_path / "j.db"),
            ingest_secret="",
            read_token="read-t",
            max_body_bytes=262144,
            return_url="https://relay.example/hook/judge-notify",
            return_secret="door-secret",
            return_max_attempts=3,
            worker_interval_seconds=0.01,
            reuse_window_seconds=3600,
            retention_days=30,
            alarm_url="",
            alarm_min_interval_seconds=600,
            ai_base_url="",
            ai_api_key="",
            ai_model="m",
            ai_timeout_seconds=5.0,
            ai_body_limit=4000,
            ai_price_in_per_1k=0.0,
            ai_price_out_per_1k=0.0,
        ),
        **overrides,
    )


@pytest.fixture
async def app_client(tmp_path):
    app = create_app(settings=_settings(tmp_path))
    async with (
        httpx.ASGITransport(app=app) as transport,
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://t") as client,
    ):
        client.app = app  # type: ignore[attr-defined]
        yield client


async def _settle(client, tries: int = 60) -> None:
    """Judging happens off the request; give the background task its turn."""
    for _ in range(tries):
        await asyncio.sleep(0.01)
        rows = await client.app.state.store.recent(1)
        if rows:
            return
    raise AssertionError("no judgement was recorded")


async def test_ingest_answers_immediately_and_judges_behind(app_client):
    """202, not the verdict: holding the sender for tens of seconds makes it
    time out and retry, so the same alert arrives twice."""
    response = await app_client.post("/events", json=EVENT)
    assert response.status_code == 202
    assert response.json()["accepted"] is True

    await _settle(app_client)
    row = (await app_client.app.state.store.recent(1))[0]
    assert row["title"] == "Single top-up over 500"
    assert row["route"] == "rule", "AI unconfigured in this fixture, so the floor answered"
    assert row["degraded_reason"] == "AI not configured", "and it says why"
    assert row["importance"] == "high"


async def test_the_judgement_is_handed_back_signed_and_correlated(app_client, monkeypatch):
    sink = Sink()
    monkeypatch.setattr(app_client.app.state, "client", sink)
    await app_client.post("/events", json=EVENT)
    await _settle(app_client)

    for _ in range(80):
        await asyncio.sleep(0.01)
        if sink.received:
            break
    assert sink.received, "a judgement nobody received is not a delivered judgement"

    sent = sink.received[0]
    assert sent["url"] == "https://relay.example/hook/judge-notify"
    stamp = sent["headers"]["X-Hook-Timestamp"]
    raw = json.dumps(sent["body"], ensure_ascii=False, sort_keys=True).encode()
    assert (
        sent["headers"]["X-Hook-Signature"]
        == hmac.new(b"door-secret", stamp.encode() + b"." + raw, hashlib.sha256).hexdigest()
    )
    assert sent["body"]["meta"]["correlation_id"] == "hr-86"
    assert sent["body"]["meta"]["brain"] == "hookjudge"
    assert sent["body"]["identity"] == {"project": "demo-alarm", "env": "prod"}

    rows = await app_client.app.state.store.recent(1)
    assert rows[0]["return_status"] == "sent"


async def test_a_refusing_pipe_is_retried_then_dead_lettered(app_client, monkeypatch):
    monkeypatch.setattr(app_client.app.state, "client", Sink(status=500))
    await app_client.post("/events", json=EVENT)
    await _settle(app_client)

    store = app_client.app.state.store
    row = (await store.recent(1))[0]
    # Drive the return leg directly rather than waiting out the backoff.
    for _ in range(3):
        await store.mark_return(int(row["id"]), "queued", int(row["return_attempts"]), None)
        rows = await store.pending_returns()
        if not rows:
            break
        await asyncio.sleep(0.05)
    final = (await store.recent(1))[0]
    assert final["return_attempts"] >= 1
    assert final["return_error"] and "http 500" in final["return_error"]


async def test_a_restatement_reuses_and_costs_nothing(app_client, monkeypatch):
    """Alert storms are the same condition restated; paying per restatement is
    paying repeatedly for one answer. (Only real AI verdicts are reusable, so
    this fixture seeds one.)"""
    store = app_client.app.state.store
    from hookjudge.contract import Incoming, Verdict

    event = Incoming.parse(EVENT, now=time.time())
    await store.record(
        event,
        Verdict(
            summary="the original verdict", importance="high", event_type="business", route="ai", model="m"
        ).normalized(),
        120,
    )

    await app_client.post("/events", json=EVENT)
    for _ in range(60):
        await asyncio.sleep(0.01)
        if len(await store.recent(5)) >= 2:
            break
    latest = (await store.recent(1))[0]
    assert latest["route"] == "reuse"
    assert latest["summary"] == "the original verdict" and latest["cost"] == 0


# Chinese markers included on purpose: inbound titles arrive decorated in
# whatever language the monitoring stack speaks.
@pytest.mark.parametrize("marker", ["[RESOLVED] {}", "{} resolved", "[已恢复] {}", "【恢复】{}"])
async def test_a_recovery_inherits_its_firings_verdict_and_never_pays(app_client, marker: str):
    """The route must be `recovery`, not `rule`.

    This assertion used to read `in ("recovery", "rule")`, which passed while
    the recovery route was unreachable: identity included the raw title, so
    "[RESOLVED] X" and "X" were two different conditions and no recovery ever
    found its firing. Every recovery fell to the rule floor and re-derived an
    importance from scratch, so a `high` alert ended with a `medium` recovery
    card — the contradiction this design exists to prevent.
    """
    store = app_client.app.state.store
    from hookjudge.contract import Incoming, Verdict

    firing = Incoming.parse(EVENT, now=time.time())
    await store.record(
        firing, Verdict(summary="the original verdict", importance="high", route="ai", model="m").normalized(), 100
    )

    ended = json.loads(json.dumps(EVENT))
    ended["event"]["title"] = marker.format("Single top-up over 500")
    await app_client.post("/events", json=ended)
    for _ in range(60):
        await asyncio.sleep(0.01)
        if any(r["is_recovery"] for r in await store.recent(5)):
            break
    recovery = next(r for r in await store.recent(5) if r["is_recovery"])
    assert recovery["route"] == "recovery", f"{marker!r} did not link to its firing"
    assert recovery["importance"] == "high", "a recovery must not contradict its own firing alert"
    assert recovery["summary"] == "the original verdict"
    assert recovery["cost"] == 0, "analysing the past is the easiest cost never to incur"


async def test_a_recovery_inherits_even_a_degraded_firing(app_client):
    """For a storm, only AI verdicts are reusable — spreading one degraded
    answer across a whole storm is worse than judging each. For a recovery the
    goal is different: agree with the alert it belongs to, degraded or not."""
    store = app_client.app.state.store
    from hookjudge.contract import Incoming, Verdict

    firing = Incoming.parse(EVENT, now=time.time())
    await store.record(
        firing,
        Verdict(
            summary="a rule verdict", importance="high", route="rule", degraded_reason="AI not configured"
        ).normalized(),
        10,
    )

    ended = json.loads(json.dumps(EVENT))
    ended["event"]["title"] = "[RESOLVED] Single top-up over 500"
    await app_client.post("/events", json=ended)
    for _ in range(60):
        await asyncio.sleep(0.01)
        if any(r["is_recovery"] for r in await store.recent(5)):
            break
    recovery = next(r for r in await store.recent(5) if r["is_recovery"])
    assert recovery["route"] == "recovery"
    assert recovery["importance"] == "high", "still must not contradict the firing"


async def test_status_and_metrics_are_guarded_and_report_the_cost_question(app_client):
    assert (await app_client.get("/status")).status_code == 401
    await app_client.post("/events", json=EVENT)
    await _settle(app_client)

    body = (await app_client.get("/status", headers={"X-Read-Token": "read-t"})).json()
    assert body["summary"]["judged"] == 1
    assert "paid_ratio_pct" in body["summary"], "the number the cost conversation turns on"
    assert body["recent"][0]["title"] == "Single top-up over 500"

    text = (await app_client.get("/metrics", headers={"Authorization": "Bearer read-t"})).text
    assert "hookjudge_up 1" in text and 'hookjudge_judgements{route="rule"}' in text


async def test_a_signed_door_refuses_strangers_and_replays(tmp_path):
    app = create_app(settings=_settings(tmp_path, ingest_secret="s3", db_path=str(tmp_path / "s.db")))
    async with (
        httpx.ASGITransport(app=app) as transport,
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://t") as client,
    ):
        body = json.dumps(EVENT).encode()
        assert (await client.post("/events", content=body)).status_code == 401

        stale = str(int(time.time()) - 3600)
        sig = hmac.new(b"s3", stale.encode() + b"." + body, hashlib.sha256).hexdigest()
        replayed = await client.post(
            "/events", content=body, headers={"X-Hook-Signature": sig, "X-Hook-Timestamp": stale}
        )
        assert replayed.status_code == 401, "a captured delivery expires"

        fresh = str(int(time.time()))
        good = hmac.new(b"s3", fresh.encode() + b"." + body, hashlib.sha256).hexdigest()
        accepted = await client.post(
            "/events", content=body, headers={"X-Hook-Signature": good, "X-Hook-Timestamp": fresh}
        )
        assert accepted.status_code == 202


async def test_a_high_byte_in_a_credential_header_is_401_not_500(tmp_path):
    """hmac.compare_digest raises TypeError on a str holding non-ASCII, and
    Starlette decodes header bytes as latin-1 — so one 0xF6 byte used to turn
    both gates into an unauthenticated HTTP 500. A wrong credential is a 401
    whatever bytes it is made of."""
    app = create_app(settings=_settings(tmp_path, ingest_secret="s3", db_path=str(tmp_path / "hb.db")))
    async with (
        httpx.ASGITransport(app=app) as transport,
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://t") as client,
    ):
        signature = await client.post(
            "/events",
            content=json.dumps(EVENT).encode(),
            headers={b"X-Hook-Signature": b"t\xf6ken", b"X-Hook-Timestamp": str(int(time.time())).encode()},
        )
        assert signature.status_code == 401

        assert (await client.get("/status", headers={b"X-Read-Token": b"t\xf6ken"})).status_code == 401
        assert (await client.get("/status", headers={b"Authorization": b"Bearer t\xf6ken"})).status_code == 401


async def test_a_malformed_body_is_a_named_refusal(app_client):
    assert (await app_client.post("/events", content=b"not json")).status_code == 400


def test_gate_matches_ci():
    """scripts/gate.sh must run what CI runs — a local list that is merely
    'close enough' is how a red CI arrives as a surprise. Adding a check to
    one requires adding it to the other in the same change.

    This test used to match by substring and named neither mypy, bandit nor
    pip-audit: three tools both files run, none of them pinned, any of them
    free to disappear from one side. And the substring `compileall -q hookjudge
    tests` was satisfied whether or not ` scripts` followed it, which is how
    this service's scripts/ directory could stop being compiled and linted with
    the contract test still green. Commands are pinned to the END of a line now.
    """
    from pathlib import Path

    here = Path(__file__).resolve().parent.parent
    gate = (here / "scripts" / "gate.sh").read_text()
    # hookjudge lives as a subdirectory of the hookrelay repo, and GitHub only
    # reads workflows from the repo ROOT — so the file this gate is pinned to
    # is one level up, under its own name.
    ci = (here.parent / ".github" / "workflows" / "ci-hookjudge.yml").read_text()

    def runs(text: str, command: str) -> bool:
        """`$PY -m mypy hookjudge` and `- run: python -m mypy hookjudge` differ
        only in how the interpreter is spelled, so the tail is the contract."""
        return any(line.rstrip().endswith(command) for line in text.splitlines())

    # Every tool the gate runs, carrying the arguments that decide what it
    # covers — ` scripts` included, because eval.py lives there and is shipped
    # advice about how to measure this service.
    for command in (
        "compileall -q hookjudge tests scripts",
        "ruff check hookjudge tests scripts",
        "ruff format --check hookjudge tests scripts",
        "mypy hookjudge",
        "bandit -q -r hookjudge",
        "pytest -q",
        "pip_audit --progress-spinner off",
    ):
        assert runs(gate, command), f"gate.sh does not run {command!r}"
        assert runs(ci, command), f"ci-hookjudge.yml does not run {command!r}"

    # Not a command: the file the inline node step reads. Its name is the only
    # evidence in either file that the step is still there.
    assert "status.html" in gate, "gate.sh no longer parses the ledger page"
    assert "status.html" in ci, "ci-hookjudge.yml no longer parses the ledger page"


class DualSink:
    """Return door that always 500s, alarm door that accepts — the exact
    situation the self-alarm exists for."""

    def __init__(self) -> None:
        self.returns = 0
        self.alarms: list[str] = []

    async def post(self, url: str, **kwargs: Any) -> Any:
        status = 200
        if "alarm.example" in url:
            self.alarms.append(str(((kwargs.get("json") or {}).get("content") or {}).get("text")))
        else:
            self.returns += 1
            status = 500

        class _R:
            status_code = status
            text = "{}"

        return _R()


async def test_dead_return_fires_the_self_alarm(tmp_path, monkeypatch):
    """When the pipe is the broken link, the news travels around it — once,
    with later dead letters folded, and never as a worker exception."""
    app = create_app(settings=_settings(tmp_path, return_max_attempts=1, alarm_url="https://alarm.example/bot"))
    async with (
        httpx.ASGITransport(app=app) as transport,
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://t") as client,
    ):
        client.app = app  # type: ignore[attr-defined]
        sink = DualSink()
        monkeypatch.setattr(app.state, "client", sink)

        await client.post("/events", json=EVENT)
        second = dict(EVENT, event=dict(EVENT["event"], title="another one that also dies"))
        await client.post("/events", json=second)

        for _ in range(200):
            await asyncio.sleep(0.01)
            if sink.returns >= 2 and sink.alarms:
                break

        assert sink.returns >= 2, "both returns were attempted and refused"
        assert len(sink.alarms) == 1, "the second dead letter folds into the window, not the channel"
        assert "verdict return dead-lettered" in sink.alarms[0]
        assert "Single top-up over 500" in sink.alarms[0]


async def test_a_recorded_verdict_wakes_every_watching_board(app_client):
    """The boards have no clock any more, so the write has to be the signal.

    The stream endpoint itself is exercised against a running service — httpx's
    ASGI transport buffers a whole response, so an endless one hangs it — and
    what matters here is that a verdict reaching the ledger reaches the watchers.
    """
    import asyncio

    live = app_client.app.state.store.on_change.__self__
    watcher = live.watch()
    try:
        assert watcher.empty()
        await app_client.post("/events", json=EVENT)
        await _settle(app_client)
        assert await asyncio.wait_for(watcher.get(), timeout=2) == "changed"
    finally:
        live.unwatch(watcher)
    assert live.watcher_count == 0


async def test_consecutive_writes_collapse_into_one_wake_up(app_client):
    """A storm must not queue a wake-up per row: the board refetches once."""
    live = app_client.app.state.store.on_change.__self__
    watcher = live.watch()
    try:
        for _ in range(5):
            live.changed()
        assert watcher.qsize() == 1
    finally:
        live.unwatch(watcher)


async def test_live_stream_needs_the_read_token(app_client):
    response = await app_client.get("/live")
    assert response.status_code == 401


async def test_a_burst_of_writes_becomes_one_wake_up_on_the_wire():
    """One alert touches a ledger many times; a board must look once, not N times."""
    import asyncio
    import json

    from hookjudge.live import Live

    live = Live()
    stream = live.stream(keepalive_seconds=5, settle_seconds=0.05).__aiter__()

    assert json.loads(await stream.__anext__())["type"] == "hello"
    for _ in range(8):  # the writes of a single alert arriving together
        live.changed()

    assert json.loads(await asyncio.wait_for(stream.__anext__(), timeout=2))["type"] == "changed"
    # Nothing is left queued behind it: the burst was absorbed, not buffered.
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(stream.__anext__(), timeout=0.3)
    await stream.aclose()
    assert live.watcher_count == 0


async def test_return_url_none_is_ledger_only_not_a_dead_letter(tmp_path):
    """The shadow deployment: the verdict's journey ends in the ledger by
    declaration. An EMPTY url stays a misconfiguration (dead + alarm) — the
    difference between "chose not to" and "forgot to" must stay visible."""
    settings = _settings(tmp_path)
    settings = replace(settings, return_url="none")
    app = create_app(settings=settings)
    async with (
        httpx.ASGITransport(app=app) as transport,
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://t") as client,
    ):
        client.app = app  # type: ignore[attr-defined]
        await client.post("/events", json=EVENT)
        await _settle(client)

        # _settle waits for the judgement; the return pass is the worker's
        # NEXT tick, so poll for the status flip rather than one sleep.
        row = {}
        for _ in range(100):
            row = (await app.state.store.recent(1))[0]
            if row["return_status"] != "queued":
                break
            await asyncio.sleep(0.01)
        assert row["return_status"] == "skipped"
        assert "ledger-only" in (row["return_error"] or "")


async def test_a_storm_burst_pays_for_exactly_one_judgement(app_client, monkeypatch):
    """The reuse route exists FOR storms, so it must work DURING one: identical
    events inside one model-latency window used to each miss the verdict the
    others had not written yet, and every restatement billed its own ai call.
    Serialized per identity, the first pays and the rest reuse."""
    calls = 0

    async def slow_ai(*args, **kwargs):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)  # wide enough that the burst overlaps it
        from hookjudge.contract import Verdict

        return Verdict(summary="s", importance="high", event_type="business", route="ai", model="m")

    monkeypatch.setattr("hookjudge.app.ai_verdict", slow_ai)

    await asyncio.gather(*(app_client.post("/events", json=EVENT) for _ in range(4)))
    store = app_client.app.state.store
    for _ in range(200):
        await asyncio.sleep(0.01)
        if len(await store.recent(10)) >= 4:
            break

    rows = await store.recent(10)
    routes = sorted(str(r["route"]) for r in rows)
    assert calls == 1, "one model call for the whole burst"
    assert routes.count("ai") == 1 and routes.count("reuse") == 3


async def test_a_disagreement_is_labeled_in_one_click_and_exports_as_an_eval_row(app_client):
    """The shadow's whole payoff: platform verdict rides in as `level`, the
    judge rules its own way, and the operator's click turns the disagreement
    into an eval row — production traffic labelling the eval set."""
    store = app_client.app.state.store
    from hookjudge.contract import Incoming, Verdict

    event = Incoming.parse(
        {"event": {"title": "gateway 5xx", "body": "x", "level": "high", "fields": {"origin": "grafana"}}},
        now=time.time(),
    )
    await store.record(event, Verdict(summary="s", importance="medium", route="ai", model="m").normalized(), 10)

    queue = (await app_client.get("/disagreements", headers={"X-Read-Token": "read-t"})).json()["queue"]
    assert len(queue) == 1 and queue[0]["level"] == "high" and queue[0]["importance"] == "medium"
    row_id = queue[0]["id"]

    labeled = await app_client.post(
        f"/judgements/{row_id}/label",
        json={"importance": "high", "source": "platform"},
        headers={"X-Read-Token": "read-t"},
    )
    assert labeled.status_code == 200

    # Labeled rows leave the queue and enter the export, harness-shaped.
    assert (await app_client.get("/disagreements", headers={"X-Read-Token": "read-t"})).json()["queue"] == []
    export = (await app_client.get("/labels/export", headers={"X-Read-Token": "read-t"})).text
    row = json.loads(export.strip())
    assert row["expect"]["importance"] == "high"
    assert row["alert"]["title"] == "gateway 5xx"
    assert row["reviewed"] is True and "platform" in row["note"]

    # Guards: vocabulary and auth.
    bad = await app_client.post(
        f"/judgements/{row_id}/label", json={"importance": "urgent"}, headers={"X-Read-Token": "read-t"}
    )
    assert bad.status_code == 400
    assert (await app_client.post(f"/judgements/{row_id}/label", json={"importance": "low"})).status_code == 401


async def test_an_agreement_is_not_a_review_item(app_client):
    store = app_client.app.state.store
    from hookjudge.contract import Incoming, Verdict

    event = Incoming.parse({"event": {"title": "ok alert", "body": "x", "level": "low"}}, now=time.time())
    await store.record(event, Verdict(summary="s", importance="low", route="ai", model="m").normalized(), 10)

    assert (await app_client.get("/disagreements", headers={"X-Read-Token": "read-t"})).json()["queue"] == []


async def test_different_rules_from_one_origin_in_one_window_share_a_burst(app_client):
    """Cross-alert correlation v1: the cascading-incident shape. Same-rule
    repeats stay the reuse route's business; recoveries never join."""
    store = app_client.app.state.store
    from hookjudge.contract import Incoming, Verdict

    def make(title, origin="prom"):
        return Incoming.parse(
            {"event": {"title": title, "body": "x", "level": "high", "fields": {"origin": origin}}},
            now=time.time(),
        )

    verdict = Verdict(summary="s", importance="high", route="ai", model="m").normalized()
    await store.record(make("disk full on db-1"), verdict, 10)
    await store.record(make("api latency p99 over 2s"), verdict, 10)
    await store.record(make("unrelated", origin="other-system"), verdict, 10)

    rows = {r["title"]: r for r in await store.recent(10)}
    assert rows["disk full on db-1"]["burst_id"], "the first member joins retroactively"
    assert rows["disk full on db-1"]["burst_id"] == rows["api latency p99 over 2s"]["burst_id"]
    assert rows["unrelated"]["burst_id"] == "", "a different origin is not this incident"


# ── interactive cards: the buttons out, the ruling back ──────────────────────


async def test_the_handed_back_verdict_carries_the_buttons_it_declared(app_client, monkeypatch):
    """A card was a dead end — readable, and nothing else. The verdict now says
    which buttons it deserves; the pipe mints the token and owns the callback,
    so nothing signed and no channel name travels in this direction."""
    sink = Sink()
    monkeypatch.setattr(app_client.app.state, "client", sink)
    await app_client.post("/events", json=EVENT)
    await _settle(app_client)
    for _ in range(80):
        await asyncio.sleep(0.01)
        if sink.received:
            break

    actions = sink.received[0]["body"]["actions"]
    assert [a["kind"] for a in actions] == ["silence", "useful", "useless"]
    # EVENT is judged `high` on the rule floor, so the mute offered is short.
    assert actions[0] == {"kind": "silence", "text": "Silence 15m", "minutes": 15}
    assert "value" not in json.dumps(actions), "no pre-signed payload: the token is the pipe's to mint"


async def test_a_returned_recovery_offers_nothing_to_silence(app_client, monkeypatch):
    """The return leg rebuilds this payload from a stored row, so it has to read
    the recovery FACT from the row rather than re-sniffing the stored title.

    This is the Alertmanager shape: the platform stated status=resolved and the
    body is a reused firing summary, so there is no recovery word anywhere in the
    text to sniff. Re-deriving it during the rebuild put "Silence 15m" on a card
    about something already over — and a window opened there lands on the next
    genuine firing.
    """
    sink = Sink()
    monkeypatch.setattr(app_client.app.state, "client", sink)
    await app_client.post(
        "/events",
        json={
            "source": "alertmanager",
            "title": "HighErrorRate",
            "body": "error ratio above 5% for 10m on pay",
            "level": "high",
            "fields": {"alertname": "HighErrorRate", "env": "prod"},
            "event_id": 501,
            "is_recovery": True,
        },
    )
    await _settle(app_client)
    for _ in range(80):
        await asyncio.sleep(0.01)
        if sink.received:
            break

    payload = sink.received[0]["body"]
    assert payload["meta"]["is_recovery"] is True
    assert payload["actions"] == [], "nothing to silence, and no ruling to ask for on a resolution notice"


async def test_a_press_is_recorded_against_the_judgement_that_interrupted_someone(app_client):
    """The half the cost figures cannot show. `useful` says being woken was worth
    it; the ledger already knew how many times it woke somebody."""
    await app_client.post("/events", json=EVENT)
    await _settle(app_client)

    pressed = await app_client.post(
        "/feedback",
        json={
            "action": {"kind": "useful", "params": {}},
            "correlation_id": "hr-86",
            "event_id": 86,
            "actor": "ou_9",
            "at": time.time(),
        },
    )
    assert pressed.status_code == 202
    assert pressed.json()["recorded"] is True and pressed.json()["mattered"] == "yes"

    attention = (await app_client.get("/status", headers={"X-Read-Token": "read-t"})).json()["summary"]["attention"]
    assert attention["interruptions"] == 1, "every judgement became a card"
    assert attention["mattered"] == 1 and attention["ruled"] == 1
    assert attention["mattered_pct"] == 100.0

    row = (await app_client.app.state.store.recent(1))[0]
    assert row["mattered"] == "yes" and row["mattered_actor"] == "ou_9"
    assert row["label_importance"] == "", "a ruling on the interruption is not an eval label"


async def test_a_redelivered_press_is_not_counted_twice(app_client):
    """The pipe retries. Pressing once must not read as pressing twice."""
    await app_client.post("/events", json=EVENT)
    await _settle(app_client)

    press = {"action": {"kind": "useless"}, "correlation_id": "hr-86", "at": 1786037727}
    first = await app_client.post("/feedback", json=press)
    again = await app_client.post("/feedback", json=press)
    assert first.json()["applied"] is True
    assert again.status_code == 202 and again.json()["applied"] is False

    attention = (await app_client.get("/status", headers={"X-Read-Token": "read-t"})).json()["summary"]["attention"]
    assert attention["did_not_matter"] == 1 and attention["ruled"] == 1


async def test_a_silence_press_is_answered_and_says_it_recorded_no_ruling(app_client):
    """Pressing "make it stop" is not saying "it did not matter" — an operator
    silences a real
    incident they are already working. Counting it as noise would corrupt the one
    number this door exists to keep honest, and answering 202 while silently
    doing nothing is the same lie as a verdict that hides its downgrade."""
    await app_client.post("/events", json=EVENT)
    await _settle(app_client)

    answered = await app_client.post(
        "/feedback", json={"action": {"kind": "silence", "params": {"minutes": 60}}, "correlation_id": "hr-86"}
    )
    assert answered.status_code == 202
    assert answered.json()["recorded"] is False and "not a ruling" in answered.json()["reason"]

    row = (await app_client.app.state.store.recent(1))[0]
    assert row["mattered"] == "", "no suppression behaviour changed and no ruling was invented"


async def test_a_press_for_a_card_nobody_judged_is_answered_not_retried_forever(app_client):
    """202 with the reason named rather than 404: a retry cannot conjure the
    judgement, and a pipe reading this as a failure would redeliver a press
    nobody can ever file."""
    answered = await app_client.post(
        "/feedback", json={"action": {"kind": "useful"}, "correlation_id": "hr-nothing-here"}
    )
    assert answered.status_code == 202
    assert answered.json() == {"recorded": False, "reason": "no judgement carries that correlation id"}


async def test_the_feedback_door_refuses_strangers_and_kinds_it_never_declared(tmp_path):
    """Same timestamped HMAC and the same secret as /events: it is the same
    sender through the same edge. A kind this brain never declares is a refusal,
    not a silent no-op — `approve` belongs to the investigator's reports."""
    app = create_app(settings=_settings(tmp_path, ingest_secret="s3", db_path=str(tmp_path / "fb.db")))
    async with (
        httpx.ASGITransport(app=app) as transport,
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://t") as client,
    ):
        body = json.dumps({"action": {"kind": "useful"}, "correlation_id": "hr-1"}).encode()
        assert (await client.post("/feedback", content=body)).status_code == 401

        stale = str(int(time.time()) - 3600)
        sig = hmac.new(b"s3", stale.encode() + b"." + body, hashlib.sha256).hexdigest()
        replayed = await client.post(
            "/feedback", content=body, headers={"X-Hook-Signature": sig, "X-Hook-Timestamp": stale}
        )
        assert replayed.status_code == 401, "a captured press expires"

        def signed(payload: dict[str, Any]) -> tuple[bytes, dict[str, str]]:
            raw = json.dumps(payload).encode()
            fresh = str(int(time.time()))
            return raw, {
                "X-Hook-Signature": hmac.new(b"s3", fresh.encode() + b"." + raw, hashlib.sha256).hexdigest(),
                "X-Hook-Timestamp": fresh,
            }

        raw, headers = signed({"action": {"kind": "useful"}, "correlation_id": "hr-1"})
        assert (await client.post("/feedback", content=raw, headers=headers)).status_code == 202

        raw, headers = signed({"action": {"kind": "approve"}, "correlation_id": "hr-1"})
        refused = await client.post("/feedback", content=raw, headers=headers)
        assert refused.status_code == 400 and "kind must be one of" in refused.json()["detail"]

        raw, headers = signed({"correlation_id": "hr-1"})
        assert (await client.post("/feedback", content=raw, headers=headers)).status_code == 400


async def test_status_and_metrics_account_for_attention_not_only_spend(app_client):
    """A condition judged three times interrupted a human three times even though
    two of them were free. The noisiest view is what says where to go turn
    something off; only the capped top of it becomes a Prometheus label, because
    an alert identity as a label value is unbounded cardinality."""
    store = app_client.app.state.store
    from hookjudge.contract import Incoming, Verdict

    def press(n: int) -> Incoming:
        return Incoming.parse(
            {"event": {"title": 'cert "prod-api" expiring', "level": "low", "fields": {"env": "prod"}}},
            now=time.time(),
            correlation_id=f"hr-{n}",
        )

    await store.record(press(1), Verdict(summary="s", importance="low", route="ai", model="m").normalized(), 900)
    for n in (2, 3):
        await store.record(press(n), Verdict(summary="s", importance="low", route="reuse").normalized(), 3)

    await app_client.post("/feedback", json={"action": {"kind": "useless"}, "correlation_id": "hr-2", "at": 100.0})

    summary = (await app_client.get("/status", headers={"X-Read-Token": "read-t"})).json()["summary"]
    attention = summary["attention"]
    assert attention["interruptions"] == summary["judged"] == 3
    assert attention["conditions"] == 1 and attention["repeats"] == 2
    assert attention["did_not_matter"] == 1 and attention["mattered_pct"] == 0.0
    noisiest = attention["noisiest"]
    assert len(noisiest) == 1 and noisiest[0]["interruptions"] == 3 and noisiest[0]["paid"] == 1

    text = (await app_client.get("/metrics", headers={"X-Read-Token": "read-t"})).text
    assert "hookjudge_interruptions 3" in text
    assert "hookjudge_conditions 1" in text
    assert "hookjudge_repeat_interruptions 2" in text
    assert 'hookjudge_attention_rulings{ruling="did_not_matter"} 1' in text
    # The identity carries the alert's own quoting; one raw quote would turn the
    # whole scrape into a parse error, so every condition's numbers vanish.
    assert 'cert \\"prod-api\\" expiring' in text
    assert "hookjudge_condition_mattered{" in text
