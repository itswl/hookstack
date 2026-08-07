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
        "title": "示例充值超500告警",
        "body": "用户 42 充值 920 元",
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
    assert row["title"] == "示例充值超500告警"
    assert row["route"] == "rule", "AI unconfigured in this fixture, so the floor answered"
    assert row["degraded_reason"] == "AI 未配置", "and it says why"
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
        Verdict(summary="原始判断", importance="high", event_type="business", route="ai", model="m").normalized(),
        120,
    )

    await app_client.post("/events", json=EVENT)
    for _ in range(60):
        await asyncio.sleep(0.01)
        if len(await store.recent(5)) >= 2:
            break
    latest = (await store.recent(1))[0]
    assert latest["route"] == "reuse"
    assert latest["summary"] == "原始判断" and latest["cost"] == 0


@pytest.mark.parametrize("marker", ["[RESOLVED] {}", "[已恢复] {}", "{} 已恢复", "【恢复】{}"])
async def test_a_recovery_inherits_its_firings_verdict_and_never_pays(app_client, marker: str):
    """The route must be `recovery`, not `rule`.

    This assertion used to read `in ("recovery", "rule")`, which passed while
    the recovery route was unreachable: identity included the raw title, so
    "[已恢复] X" and "X" were two different conditions and no recovery ever
    found its firing. Every recovery fell to the rule floor and re-derived an
    importance from scratch, so a `high` alert ended with a `medium` recovery
    card — the contradiction this design exists to prevent.
    """
    store = app_client.app.state.store
    from hookjudge.contract import Incoming, Verdict

    firing = Incoming.parse(EVENT, now=time.time())
    await store.record(firing, Verdict(summary="原始判断", importance="high", route="ai", model="m").normalized(), 100)

    ended = json.loads(json.dumps(EVENT))
    ended["event"]["title"] = marker.format("示例充值超500告警")
    await app_client.post("/events", json=ended)
    for _ in range(60):
        await asyncio.sleep(0.01)
        if any(r["is_recovery"] for r in await store.recent(5)):
            break
    recovery = next(r for r in await store.recent(5) if r["is_recovery"])
    assert recovery["route"] == "recovery", f"{marker!r} did not link to its firing"
    assert recovery["importance"] == "high", "a recovery must not contradict its own firing alert"
    assert recovery["summary"] == "原始判断"
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
        Verdict(summary="规则判定", importance="high", route="rule", degraded_reason="AI 未配置").normalized(),
        10,
    )

    ended = json.loads(json.dumps(EVENT))
    ended["event"]["title"] = "[已恢复] 示例充值超500告警"
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
    assert body["recent"][0]["title"] == "示例充值超500告警"

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


async def test_a_malformed_body_is_a_named_refusal(app_client):
    assert (await app_client.post("/events", content=b"not json")).status_code == 400


def test_gate_matches_ci():
    """scripts/gate.sh must run what CI runs — a local list that is merely
    'close enough' is how a red CI arrives as a surprise. Adding a check to
    one requires adding it to the other in the same change."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    gate = (root / "scripts" / "gate.sh").read_text()
    ci = (root / ".github" / "workflows" / "ci.yml").read_text()

    for check in (
        "compileall -q hookjudge tests",
        "ruff check hookjudge tests",
        "ruff format --check hookjudge tests",
        "status.html",
        "pytest -q",
    ):
        assert check in gate, f"gate.sh is missing {check!r}"
        assert check in ci, f"ci.yml is missing {check!r}"
