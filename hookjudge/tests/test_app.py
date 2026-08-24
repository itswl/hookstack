"""The service end to end: accept fast, judge behind, hand the result back."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import sqlite3
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
            # Set, because the ruling door fails CLOSED when it is not — unlike
            # every other door here, where an empty secret means an in-network
            # hop. A test suite that left it empty would only ever see the 503.
            ruling_secret="ruling-secret",
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


# ── the promises the code makes about not losing things ───────────────────────


async def test_an_alert_that_cannot_be_recorded_is_loud_rather_than_lost(tmp_path, monkeypatch, caplog):
    """The one failure nobody else holds a copy of. The sender was answered 202
    and will not retry, and a judging failure leaves no ledger row to
    dead-letter, so an unguarded exception meant the alert simply never
    existed — traceable only through asyncio's "Task exception was never
    retrieved" on stderr, with no identity in it and no timestamp to line up."""
    caplog.set_level(logging.ERROR)
    app = create_app(settings=_settings(tmp_path, alarm_url="https://alarm.example/bot"))
    async with (
        httpx.ASGITransport(app=app) as transport,
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://t") as client,
    ):
        sink = DualSink()
        monkeypatch.setattr(app.state, "client", sink)

        async def locked(*_args: Any, **_kwargs: Any) -> int:
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(app.state.store, "record", locked)

        accepted = await client.post("/events", json=EVENT)
        assert accepted.status_code == 202, "the door still answers fast — the loss is behind it"

        for _ in range(300):
            await asyncio.sleep(0.01)
            if sink.alarms:
                break
        assert sink.alarms, "a lost alert reaches the one channel that does not run through the pipe"
        assert "no verdict was recorded" in sink.alarms[0], "and it names ITS failure, not the return leg's"
        assert "Single top-up over 500" in sink.alarms[0]
        assert "database is locked" in sink.alarms[0]
        assert "judged nowhere" in caplog.text, "a dropped alert is worth a log line too"

        # The docstring's promise, checked where it is made: it returns, and the
        # 0 says no row was written.
        from hookjudge.contract import Incoming

        event = Incoming.parse(EVENT, now=time.time())
        assert await app.state.judge_and_record(sink, event) == 0


async def test_a_non_http_failure_on_the_return_leg_still_counts_as_an_attempt(tmp_path, monkeypatch):
    """It caught httpx.HTTPError alone, so anything else escaped to the worker's
    catch-all: the row stayed `queued` with return_attempts unchanged, which
    means it never backed off and never died. pending_returns is ORDER BY id
    LIMIT 50, so that row holds a queue slot forever."""
    app = create_app(settings=_settings(tmp_path, return_max_attempts=1, alarm_url="https://alarm.example/bot"))
    async with (
        httpx.ASGITransport(app=app) as transport,
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://t") as client,
    ):

        class Broken:
            """Fails the return in a way httpx.HTTPError does not cover."""

            def __init__(self) -> None:
                self.alarms: list[str] = []

            async def post(self, url: str, **kwargs: Any) -> Any:
                if "alarm.example" in url:
                    self.alarms.append(str(((kwargs.get("json") or {}).get("content") or {}).get("text")))

                    class _R:
                        status_code = 200
                        text = "{}"

                    return _R()
                raise RuntimeError("event loop is closed")

        sink = Broken()
        monkeypatch.setattr(app.state, "client", sink)
        await client.post("/events", json=EVENT)

        store = app.state.store
        for _ in range(300):
            await asyncio.sleep(0.01)
            rows = await store.recent(1)
            if rows and rows[0]["return_status"] != "queued":
                break
        row = (await store.recent(1))[0]
        assert row["return_attempts"] == 1, "a failure we cannot name is still a failed attempt"
        assert row["return_status"] == "dead", "so the dead-letter path can reach it"
        assert "RuntimeError" in row["return_error"], "named by class, not called a transport error it was not"
        assert sink.alarms and "dead-lettered" in sink.alarms[0]
        assert row["return_attempted_at"], "and the attempt clock the backoff measures from is set"


async def test_a_row_that_cannot_be_marked_does_not_starve_the_queue_behind_it(app_client, monkeypatch):
    """The return queue is ORDER BY id LIMIT 50, so the broken row is first
    again on every tick. Letting its failure out abandoned every row behind it
    in that tick — and in every tick after it, which is starvation, not a
    retry."""
    store = app_client.app.state.store
    sink = Sink()
    monkeypatch.setattr(app_client.app.state, "client", sink)
    real_mark = store.mark_return

    async def flaky(
        row_id: int, status: str, attempts: int, error: str | None, *, attempted_at: float | None = None
    ) -> None:
        # A fresh ledger per test, so the first judgement is id 1.
        if row_id == 1:
            raise sqlite3.OperationalError("database is locked")
        await real_mark(row_id, status, attempts, error, attempted_at=attempted_at)

    monkeypatch.setattr(store, "mark_return", flaky)

    await app_client.post("/events", json=EVENT)
    await _settle(app_client)
    behind = dict(EVENT, event=dict(EVENT["event"], title="the one queued behind it"))
    await app_client.post("/events", json=behind)

    rows: dict[str, Any] = {}
    for _ in range(400):
        await asyncio.sleep(0.01)
        rows = {r["title"]: r for r in await store.recent(5)}
        if rows.get("the one queued behind it", {}).get("return_status") == "sent":
            break
    assert rows["the one queued behind it"]["return_status"] == "sent", "the row behind the broken one still went"
    assert rows["Single top-up over 500"]["return_status"] == "queued", "and the broken one is still owed a mark"


async def test_the_label_write_and_the_bulk_export_disable_themselves_when_unguarded(tmp_path):
    """Reads with no token configured stay open — deliberate dev mode across this
    family. A write cannot borrow that rule, and hookrelay already settled the
    split for all three services (see its security.py token_ok: "dev mode for
    read, endpoint disabled for admin"). Unconfigured, these two let whoever
    found the port rewrite the labels the eval set is built from and download
    every alert body in the ledger."""
    app = create_app(settings=_settings(tmp_path, read_token="", db_path=str(tmp_path / "open.db")))
    async with (
        httpx.ASGITransport(app=app) as transport,
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://t") as client,
    ):
        assert (await client.get("/status")).status_code == 200, "dev mode for read does not change"
        assert (await client.get("/disagreements")).status_code == 200
        assert (await client.get("/metrics")).status_code == 200

        # 403, not 401: no credential opens this, so inviting one is a lie.
        labeled = await client.post("/judgements/1/label", json={"importance": "high"})
        assert labeled.status_code == 403
        assert "HOOKJUDGE_READ_TOKEN" in labeled.json()["detail"], "and it says what would enable it"
        assert (await client.get("/labels/export")).status_code == 403

        # /feedback is signature-authenticated like /events — the same sender
        # through the same edge — not token-guarded. It is not in this category.
        pressed = await client.post("/feedback", json={"action": {"kind": "useful"}, "correlation_id": "hr-x"})
        assert pressed.status_code == 202


async def test_a_busy_ledger_does_not_hide_the_peers_of_a_burst(app_client, monkeypatch):
    """The origin match once ran in Python over "the last 50 rows in the
    window", so unrelated traffic between two rules from one origin hid every
    peer either of them had — and cross-alert grouping failed on exactly the
    busy ledger it exists for. Both filters are in the query now; the LIMIT
    caps the peers instead of the search, which only a ledger busier than that
    limit can show."""
    store = app_client.app.state.store
    monkeypatch.setattr(app_client.app.state, "client", Sink())
    from hookjudge.contract import Incoming, Verdict

    verdict = Verdict(summary="s", importance="high", route="ai", model="m").normalized()

    def alert(title: str, origin: str) -> Incoming:
        return Incoming.parse(
            {"event": {"title": title, "body": "x", "level": "high", "fields": {"origin": origin}}},
            now=time.time(),
        )

    await store.record(alert("disk full on db-1", "prom"), verdict, 10)
    # More unrelated rows in the window than the peer LIMIT, each from an origin
    # of its own so none of them form a burst either.
    for n in range(60):
        await store.record(alert(f"unrelated rule {n}", f"other-system-{n}"), verdict, 10)
    await store.record(alert("api latency p99 over 2s", "prom"), verdict, 10)

    rows = {r["title"]: r for r in await store.recent(200)}
    burst = rows["api latency p99 over 2s"]["burst_id"]
    assert burst, "the second rule found its peer through 60 rows of unrelated traffic"
    assert rows["disk full on db-1"]["burst_id"] == burst, "and the first member joined it retroactively"
    assert rows["unrelated rule 7"]["burst_id"] == "", "a different origin is not this incident"


async def test_a_condition_that_heals_itself_is_visible_without_anyone_pressing(app_client):
    """The signal that needs no human.

    `mattered` is empty until somebody presses a button, and on an unattended
    deployment nobody does — which leaves the attention figures permanently
    uncalibrated. Pairing each firing with the recovery that followed it is the
    proxy available from data the ledger already keeps: measured on production,
    two conditions accounted for over half the traffic and healed themselves in a
    median of five minutes, while `DatasourceNoData` fired seventeen times and
    never recovered once. Identical on the cost figures; opposite here.

    It is a PROXY and stays in its own keys. A human saying "not worth waking me"
    is evidence; a five-minute self-heal is a hint pointing at the same place, and
    reporting the second as the first would be the ledger claiming somebody spoke.
    """
    store = app_client.app.state.store
    from hookjudge.contract import Incoming, Verdict

    def event(title: str, at: float, *, recovery: bool) -> Incoming:
        return Incoming(
            source="grafana",
            title=title,
            body="b",
            level="high",
            fields={},
            correlation_id=f"hr-{at:.0f}",
            received_at=at,
            recovery_flag=recovery,
        )

    async def record(title: str, at: float, *, recovery: bool = False) -> None:
        await store.record(event(title, at, recovery=recovery), Verdict(summary="s", importance="high").normalized(), 1)

    base = time.time() - 3600
    # Flapping: fires and heals in three minutes, twice.
    await record("Topup over 500", base)
    await record("Topup over 500", base + 180, recovery=True)
    await record("Topup over 500", base + 600)
    await record("Topup over 500", base + 780, recovery=True)
    # A restatement BEFORE the recovery. The clock must not restart on it, or a
    # storm would look like it healed in the gap between its last two cards.
    await record("Disk will fill", base + 100)
    await record("Disk will fill", base + 1900)  # same condition, still open
    await record("Disk will fill", base + 2000, recovery=True)
    # Never recovers — the shape of a real, unfixed problem.
    await record("DatasourceNoData", base + 200)
    await record("DatasourceNoData", base + 400)

    healing = await store.self_healing(base - 60)

    topup = healing["grafana|Topup over 500"]
    assert topup["self_resolved"] == 2 and topup["fired"] == 2
    assert topup["median_seconds"] == 180.0
    assert topup["likely_flapping"] is True

    disk = healing["grafana|Disk will fill"]
    assert disk["self_resolved"] == 1
    # 1900 seconds from the FIRST firing, not 100 from the restatement.
    assert disk["median_seconds"] == 1900.0
    assert disk["likely_flapping"] is False, "half an hour open is not a flap"

    dead = healing["grafana|DatasourceNoData"]
    assert dead["self_resolved"] == 0 and dead["median_seconds"] is None
    assert dead["likely_flapping"] is False, "never recovering is the opposite of flapping"

    # And it reaches the board without being confused for a ruling.
    body = (await app_client.get("/status", headers={"X-Read-Token": "read-t"})).json()
    attention = body["summary"]["attention"]
    assert attention["likely_flapping"] == 1
    assert attention["ruled"] == 0, "nobody pressed anything, and the rulings say so"
    noisy = {row["title"]: row for row in attention["noisiest"]}
    assert noisy["Topup over 500"]["likely_flapping"] is True
    assert noisy["Topup over 500"]["mattered"] == 0, "a flap is not a human verdict"

    # The row has to be checkable by whoever doubts it, and it was not. Four
    # firings and four recoveries make `interruptions` eight, while the verdict
    # divided episodes by FIRINGS — so the two visible numbers said 2/8 next to
    # `likely_flapping: true` and the flag read as broken. It was right; the
    # denominator was simply missing. Production made this loud: 77 beside 30,
    # on a comparison that was actually 30 of 47.
    row = noisy["Topup over 500"]
    assert row["interruptions"] == 4, "every card, recoveries included"
    assert row["fired"] == 2, "the denominator the verdict used, now visible"
    assert row["self_resolved"] * 2 >= row["fired"], "and the arithmetic checks out on the page"


async def test_an_ai_ruling_never_lands_in_the_column_that_means_a_person_said_so(tmp_path):
    """The LLM adjudicates the data gate. It does not get to be a human.

    `mattered` is the only field in this ledger that means somebody spoke; `ruled`
    counts it and `mattered_pct` divides by it. So the AI verdict gets its own
    door, its own table and its own keys, and the test that matters is the
    negative one: after a model rules on every condition, `ruled` is still 0.

    The unit differs too. A person rules on the card that woke them at 3am; a
    model reading twenty case files rules on the CONDITION behind them. One row
    per condition, latest ruling wins — a standing read of evidence that keeps
    arriving, unlike a press, which is a fact about a moment.
    """
    app = create_app(
        settings=_settings(tmp_path, ingest_secret="s4", ruling_secret="r4", db_path=str(tmp_path / "ai.db"))
    )

    def sign(payload: dict, *, secret: bytes = b"r4") -> tuple[bytes, dict[str, str]]:
        body = json.dumps(payload).encode()
        ts = str(int(time.time()))
        sig = hmac.new(secret, ts.encode() + b"." + body, hashlib.sha256).hexdigest()
        return body, {"X-Hook-Signature": sig, "X-Hook-Timestamp": ts}

    async with (
        httpx.ASGITransport(app=app) as transport,
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://t") as client,
    ):
        store = app.state.store
        from hookjudge.contract import Incoming, Verdict

        base = time.time() - 3600
        for index in range(4):
            await store.record(
                Incoming(
                    source="grafana",
                    title="Topup over 500",
                    body="b",
                    level="high",
                    fields={},
                    correlation_id=f"c{index}",
                    received_at=base + index * 60,
                    recovery_flag=False,
                ),
                Verdict(summary="s", importance="high").normalized(),
                1,
            )

        good = {
            "identity": "grafana|Topup over 500",
            "verdict": "not_worth_it",
            "why": "47 firings, every investigation concluded the rule fired correctly on real business volume",
            "model": "claude-opus-5",
        }
        body, headers = sign(good)
        assert (await client.post("/rulings/ai", content=body)).status_code == 401, "the door is signed"

        # The ingest secret must NOT open it. That secret also opens /events, so
        # a component that can sign for it can forge judgements — and the caller
        # here is the investigator, the one that reads attacker-influenced text.
        # One door, one credential, and this is the assertion that keeps it true.
        wrong_body, wrong_headers = sign(good, secret=b"s4")
        refused = await client.post("/rulings/ai", content=wrong_body, headers=wrong_headers)
        assert refused.status_code == 401, "the ingest credential is not a ruling credential"

        assert (await client.post("/rulings/ai", content=body, headers=headers)).status_code == 202

        # A verdict with no reasoning is an opinion, and `likely_flapping`
        # already says something true without words.
        body, headers = sign({**good, "why": "   "})
        assert (await client.post("/rulings/ai", content=body, headers=headers)).status_code == 400
        body, headers = sign({**good, "verdict": "meh"})
        assert (await client.post("/rulings/ai", content=body, headers=headers)).status_code == 400
        # An empty identity is a valid primary key, so this has to be refused
        # BEFORE the write, not after it.
        body, headers = sign({**good, "identity": "  "})
        assert (await client.post("/rulings/ai", content=body, headers=headers)).status_code == 400

        # Latest wins: the evidence keeps arriving, so a condition that stopped
        # self-resolving must be re-rulable.
        body, headers = sign({**good, "verdict": "worth_it", "why": "it stopped self-resolving on Aug 20"})
        assert (await client.post("/rulings/ai", content=body, headers=headers)).status_code == 202

        attention = (await client.get("/status", headers={"X-Read-Token": "read-t"})).json()["summary"]["attention"]

    assert attention["ai_ruled"] == 1, "one condition ruled, not four cards"
    assert attention["ruled"] == 0, "nobody pressed anything, and the human column says so"
    assert attention["mattered"] == 0 and attention["mattered_pct"] is None
    row = {r["title"]: r for r in attention["noisiest"]}["Topup over 500"]
    assert row["ai_ruling"] == "worth_it", "the later ruling replaced the earlier one"
    assert "stopped self-resolving" in row["ai_why"], "the reason travels with the verdict"
    assert row["mattered"] == 0, "and it did not leak into the human column"


async def test_the_ruling_door_is_shut_when_its_secret_is_unset(tmp_path):
    """Fails CLOSED, which is the opposite of every other door here.

    Elsewhere an empty secret means "an in-network hop between two containers of
    one deployment" and verify_signature waves it through — the judge's own
    return door is configured exactly that way. This door writes to the ledger on
    behalf of the component that reads attacker-influenced text, so unconfigured
    has to mean shut rather than open to anyone on the network.
    """
    app = create_app(settings=_settings(tmp_path, ruling_secret="", db_path=str(tmp_path / "shut.db")))
    async with (
        httpx.ASGITransport(app=app) as transport,
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://t") as client,
    ):
        answer = await client.post("/rulings/ai", json={"identity": "x", "verdict": "worth_it", "why": "y"})

    assert answer.status_code == 503
    assert "HOOKJUDGE_RULING_SECRET" in answer.json()["detail"], "say which knob opens it"


async def test_an_ai_ruling_on_a_condition_outside_the_window_is_not_counted(tmp_path):
    """`ruled` counts presses inside the window; `ai_ruled` counted every row.

    Nothing validates that an identity exists in the ledger, so a ruling on a
    condition this deployment has never seen inflated the total while appearing
    in no row of `noisiest`. Two numbers side by side with different denominators
    is the defect this board spent a week removing from itself.

    The ruling itself stays standing — re-reading it weekly to keep it in the
    window would be pointless. It is the COUNT that has to answer the same
    question the number beside it answers.
    """
    app = create_app(
        settings=_settings(tmp_path, ruling_secret="r5", db_path=str(tmp_path / "win.db"), retention_days=0)
    )

    def sign(payload: dict) -> tuple[bytes, dict[str, str]]:
        body = json.dumps(payload).encode()
        ts = str(int(time.time()))
        return body, {
            "X-Hook-Signature": hmac.new(b"r5", ts.encode() + b"." + body, hashlib.sha256).hexdigest(),
            "X-Hook-Timestamp": ts,
        }

    async with (
        httpx.ASGITransport(app=app) as transport,
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://t") as client,
    ):
        store = app.state.store
        from hookjudge.contract import Incoming, Verdict

        # One condition, twice, inside the window.
        for index in range(2):
            await store.record(
                Incoming(
                    source="grafana",
                    title="Inside the window",
                    body="b",
                    level="high",
                    fields={},
                    correlation_id=f"w{index}",
                    received_at=time.time() - 600 + index,
                    recovery_flag=False,
                ),
                Verdict(summary="s", importance="high").normalized(),
                1,
            )

        for identity in ("grafana|Inside the window", "grafana|Never seen here"):
            body, headers = sign(
                {"identity": identity, "verdict": "not_worth_it", "why": "three cases, one route", "model": "m"}
            )
            assert (await client.post("/rulings/ai", content=body, headers=headers)).status_code == 202

        body = (await client.get("/status?window_hours=1", headers={"X-Read-Token": "read-t"})).json()
        # Inside the lifespan: the orphan is still ON RECORD, dropped from a
        # count and not from the table.
        assert len(await store.ai_rulings()) == 2

    attention = body["summary"]["attention"]
    assert attention["ai_ruled"] == 1, "the orphan ruling is not in this window's total"
    titles = {row["title"] for row in attention["noisiest"]}
    assert "Inside the window" in titles and "Never seen here" not in titles


async def test_the_second_axis_is_counted_apart_from_importance_and_from_a_person(tmp_path):
    """`importance` came back 'high' for 210 of 216 alerts on production.

    A classifier that answers the same word 97% of the time carries almost no
    information, and its own prompt explains it: 74% of that traffic is payments,
    and payments default to high. The question the product needs — does anyone
    have to act — was never being asked.

    So `wake_someone` is asked, and kept apart from both neighbours: `importance`
    is a different question, `mattered` means a HUMAN said so. Three fields, three
    counts, no averaging — because the point of the number is to be able to say
    "this one also answers yes every time, stop paying for it", and an average
    would hide exactly that.
    """
    app = create_app(settings=_settings(tmp_path, db_path=str(tmp_path / "wake.db")))
    async with (
        httpx.ASGITransport(app=app) as transport,
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://t") as client,
    ):
        store = app.state.store
        from hookjudge.contract import Incoming, Verdict

        base = time.time() - 600
        # Same importance on all three; the second axis disagrees with it twice.
        for index, wake in enumerate(("yes", "no", "")):
            await store.record(
                Incoming(
                    source="grafana",
                    title=f"示例条件 {index}",
                    body="b",
                    level="high",
                    fields={},
                    correlation_id=f"w{index}",
                    received_at=base + index,
                    recovery_flag=False,
                ),
                Verdict(summary="s", importance="high", wake_someone=wake).normalized(),
                1,
            )

        body = (await client.get("/status", headers={"X-Read-Token": "read-t"})).json()
        metrics = (await client.get("/metrics", headers={"X-Read-Token": "read-t"})).text

    a = body["summary"]["attention"]
    assert (a["wake_yes"], a["wake_no"], a["wake_answered"]) == (1, 1, 2)
    # The unanswered one is neither, and must not be read as a quiet night.
    assert a["interruptions"] == 3 and a["wake_answered"] == 2

    # And it has not leaked into either neighbour.
    assert a["ruled"] == 0 and a["mattered"] == 0, "a model answering is not a person answering"
    assert a["mattered_pct"] is None

    assert 'hookjudge_wake_someone{answer="yes"} 1' in metrics
    assert 'hookjudge_wake_someone{answer="no"} 1' in metrics
    assert "hookjudge_quiet_regrets 0" in metrics, "no person has contradicted a quiet yet"


async def test_a_quiet_the_person_contradicts_is_counted_as_a_regret(tmp_path):
    """The quiet stage drops cards on wake=no. The one failure that counts is a
    person later ruling that interruption mattered — and a delivery policy that
    cannot see its own regrets is a policy nobody can argue with. One row,
    wake=no then mattered=yes, must surface in attention and in /metrics."""
    app = create_app(settings=_settings(tmp_path, db_path=str(tmp_path / "regret.db")))
    async with (
        httpx.ASGITransport(app=app) as transport,
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://t") as client,
    ):
        store = app.state.store
        from hookjudge.contract import Incoming, Verdict

        await store.record(
            Incoming(
                source="grafana",
                title="示例条件",
                body="b",
                level="high",
                fields={},
                correlation_id="regret-1",
                received_at=time.time(),
                recovery_flag=False,
            ),
            Verdict(summary="s", importance="high", wake_someone="no").normalized(),
            1,
        )
        await store.record_mattered("regret-1", mattered="yes", at=time.time(), actor="ou_test")
        body = (await client.get("/status", headers={"X-Read-Token": "read-t"})).json()
        metrics = (await client.get("/metrics", headers={"X-Read-Token": "read-t"})).text

    a = body["summary"]["attention"]
    assert a["quiet_regrets"] == 1
    assert a["wake_no"] == 1 and a["mattered"] == 1
    assert "hookjudge_quiet_regrets 1" in metrics


def test_an_unanswered_second_axis_is_blank_not_a_no() -> None:
    """A parse failure must not become a vote for a quiet night."""
    from hookjudge.contract import Verdict

    assert Verdict(summary="s", importance="high", wake_someone="YES ").normalized().wake_someone == "yes"
    assert Verdict(summary="s", importance="high", wake_someone="No").normalized().wake_someone == "no"
    for junk in ("", "maybe", "true", "1"):
        assert Verdict(summary="s", importance="high", wake_someone=junk).normalized().wake_someone == ""


async def test_the_return_leg_rebuild_carries_the_wake_answer(app_client, monkeypatch):
    """The delivered payload, not the Outgoing class. The first wake=no on
    production reached the pipe as '' and was routed into a card, because the
    return leg is the THIRD place a Verdict is constructed — from the stored
    row — and a field missing there is defaulted no matter what the ledger
    says. A test on Outgoing alone proved the two sites that already worked.
    """
    sink = Sink()
    monkeypatch.setattr(app_client.app.state, "client", sink)
    store = app_client.app.state.store
    from hookjudge.contract import Incoming, Verdict

    await store.record(
        Incoming(
            source="grafana",
            title="DatasourceNoData",
            body="value=null",
            level="high",
            fields={},
            correlation_id="w-return",
            received_at=time.time(),
            recovery_flag=False,
        ),
        Verdict(summary="config misreport", importance="medium", wake_someone="no").normalized(),
        1,
    )
    for _ in range(200):
        await asyncio.sleep(0.01)
        if sink.received:
            break

    meta = sink.received[0]["body"]["meta"]
    assert meta["wake_someone"] == "no", "the row said no; the wire must say no"
