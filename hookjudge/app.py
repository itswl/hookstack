"""hookjudge — a brain behind a pipe. It judges; it does nothing else.

    POST /events    the pipe's normalized event. Answers 202 immediately.
    GET  /status    the ledger: verdicts, routes, cost, return state
    GET  /metrics   Prometheus text
    GET  /healthz

Why 202 and not the verdict: judging takes tens of seconds. Holding the pipe's
connection open for that makes the SENDER time out and retry, so you get the
same alert twice while the first copy is still being analysed. The judgement
goes back the other way, to a pipe door, once it exists.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from hookjudge.contract import Incoming, Outgoing
from hookjudge.judge import ai_verdict, reuse_verdict, rule_verdict
from hookjudge.settings import Settings
from hookjudge.store import Store, now_ts

_BACKOFF_SECONDS = (5, 30, 120, 600, 1800)


def verify_signature(secret: str, body: bytes, provided: str | None, timestamp: str | None, now: float) -> bool:
    """Same scheme the pipe speaks: HMAC over "{ts}.{body}" when a timestamp is
    present (so a captured delivery expires), body-only otherwise."""
    if not secret:
        return True
    if not provided:
        return False
    given = provided.strip().removeprefix("sha256=").lower()
    if timestamp:
        try:
            sent_at = float(timestamp)
        except ValueError:
            return False
        if abs(now - sent_at) > 300:
            return False
        expected = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, given)
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, given)


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or Settings.load()
    store = Store(app_settings.db_path)

    async def _judge_and_record(client: httpx.AsyncClient, event: Incoming) -> int:
        """One event, one verdict, one row. Never raises into the caller."""
        started = time.monotonic()
        prior = await store.prior_verdict(
            event.identity,
            app_settings.reuse_window_seconds,
            now_ts(),
            # A recovery inherits its firing's verdict whatever route produced
            # it: the point is not to save a call but to agree with the alert
            # this recovery belongs to.
            any_route=event.is_recovery,
        )
        if prior is not None:
            verdict = reuse_verdict(prior, recovery=event.is_recovery)
        elif event.is_recovery:
            # An ended condition with nothing to reuse: rules, not a model. The
            # question "how bad is it" is moot once it is over, and paying to
            # analyse the past is the easiest cost to never incur.
            verdict = rule_verdict(event, degraded_reason="恢复告警不单独分析")
        else:
            verdict = await ai_verdict(client, app_settings, event)
        latency_ms = int((time.monotonic() - started) * 1000)
        return await store.record(event, verdict, latency_ms)

    async def _return_once(client: httpx.AsyncClient, row: dict[str, Any], now: float) -> None:
        """Hand one judgement back to the pipe. The only delivery this service
        does, and only to one address — fan-out is the pipe's job."""
        if not app_settings.return_url:
            await store.mark_return(int(row["id"]), "dead", int(row["return_attempts"]), "HOOKJUDGE_RETURN_URL 未配置")
            return
        event = Incoming(
            source=str(row["source"]),
            title=str(row["title"]),
            body=str(row["body"]),
            level="",
            fields=json.loads(row["fields_json"] or "{}"),
            raw={},
            correlation_id=str(row["correlation_id"] or ""),
            received_at=float(row["received_at"]),
        )
        from hookjudge.contract import Verdict

        verdict = Verdict(
            summary=str(row["summary"]),
            importance=str(row["importance"]),
            event_type=str(row["event_type"]),
            impact_scope=str(row["impact_scope"]),
            route=str(row["route"]),
        )
        payload = Outgoing(incoming=event, verdict=verdict).payload()
        payload["meta"]["is_recovery"] = bool(row["is_recovery"])
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
        headers = {"content-type": "application/json"}
        if app_settings.return_secret:
            stamp = str(int(now))
            headers["X-Hook-Timestamp"] = stamp
            headers["X-Hook-Signature"] = hmac.new(
                app_settings.return_secret.encode(), stamp.encode() + b"." + body, hashlib.sha256
            ).hexdigest()
        attempts = int(row["return_attempts"]) + 1
        try:
            response = await client.post(app_settings.return_url, content=body, headers=headers, timeout=10.0)
            if response.status_code < 300:
                await store.mark_return(int(row["id"]), "sent", attempts, None)
                return
            error = f"http {response.status_code}: {response.text[:200]}"
        except httpx.HTTPError as exc:
            error = f"transport: {exc.__class__.__name__}"
        status = "queued" if attempts < app_settings.return_max_attempts else "dead"
        await store.mark_return(int(row["id"]), status, attempts, error)

    async def _worker() -> None:
        """Reads the client from app.state rather than capturing it.

        A captured reference is not a seam: nothing can stand in for the
        outbound client — not a test, not a future proxy — and a component
        with no seam is a component you can only observe in production.
        """
        next_purge = 0.0
        while True:
            try:
                now = now_ts()
                client = app.state.client
                for row in await store.pending_returns():
                    # Backoff by attempt count, so a pipe that is down is
                    # retried patiently rather than hammered.
                    wait = _BACKOFF_SECONDS[min(int(row["return_attempts"]), len(_BACKOFF_SECONDS) - 1)]
                    if int(row["return_attempts"]) and now - float(row["received_at"]) < wait:
                        continue
                    await _return_once(client, row, now)
                if app_settings.retention_days > 0 and now >= next_purge:
                    next_purge = now + 3600
                    await store.purge_older_than(now - app_settings.retention_days * 86400)
            except Exception as error:  # noqa: BLE001 — the loop must survive anything
                print(f"[hookjudge] worker: {error.__class__.__name__}: {error}")
            await asyncio.sleep(app_settings.worker_interval_seconds)

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await store.open()
        client = httpx.AsyncClient()
        app.state.client = client
        worker = asyncio.create_task(_worker())
        try:
            yield
        finally:
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker
            await client.aclose()
            await store.close()

    app = FastAPI(title="hookjudge", lifespan=lifespan)
    app.state.settings = app_settings
    app.state.store = store
    app.state.judge_and_record = _judge_and_record

    def _read_guard(token: str | None, authorization: str | None) -> None:
        configured = app_settings.read_token
        if not configured:
            return
        bearer = authorization[7:].strip() if authorization and authorization.lower().startswith("bearer ") else None
        if not (
            (token and hmac.compare_digest(configured, token)) or (bearer and hmac.compare_digest(configured, bearer))
        ):
            raise HTTPException(status_code=401, detail="read token required")

    @app.post("/events")
    async def ingest(
        request: Request,
        x_hook_signature: str | None = Header(default=None),
        x_hook_timestamp: str | None = Header(default=None),
        x_hook_correlation_id: str | None = Header(default=None),
        x_request_id: str | None = Header(default=None),
    ) -> JSONResponse:
        body = await request.body()
        if len(body) > app_settings.max_body_bytes:
            raise HTTPException(status_code=413, detail="body too large")
        if not verify_signature(app_settings.ingest_secret, body, x_hook_signature, x_hook_timestamp, now_ts()):
            raise HTTPException(status_code=401, detail="bad signature")
        try:
            payload = json.loads(body or b"{}")
        except ValueError:
            raise HTTPException(status_code=400, detail="body is not JSON") from None
        # The pipe stamps the id on the transport, not in the signed body, so
        # it must be read here or the round trip cannot be reassembled. Both
        # names, because allowlist-minded proxies keep X-Request-Id.
        event = Incoming.parse(
            payload if isinstance(payload, dict) else {},
            now=now_ts(),
            correlation_id=(x_hook_correlation_id or x_request_id or "").strip(),
        )

        # Judged in the background: the answer takes tens of seconds and the
        # sender must not be held for it.
        asyncio.create_task(_judge_and_record(app.state.client, event))
        return JSONResponse({"accepted": True, "identity": event.identity}, status_code=202)

    @app.get("/status")
    async def status(
        x_read_token: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        route: str | None = None,
        q: str | None = None,
        limit: int = 50,
        window_hours: int = 24,
    ) -> dict[str, Any]:
        _read_guard(x_read_token, authorization)
        return {
            "summary": await store.summary(now_ts() - max(1, window_hours) * 3600),
            "recent": await store.recent(limit, route=route, q=q),
        }

    @app.get("/metrics", response_class=PlainTextResponse)
    async def metrics(
        x_read_token: str | None = Header(default=None), authorization: str | None = Header(default=None)
    ) -> str:
        _read_guard(x_read_token, authorization)
        data = await store.summary(now_ts() - 86400)
        lines = [
            "# HELP hookjudge_up Judge process is serving.",
            "# TYPE hookjudge_up gauge",
            "hookjudge_up 1",
            "# HELP hookjudge_judgements Judgements in the last 24h, by route.",
            "# TYPE hookjudge_judgements gauge",
        ]
        for name, stats in sorted(data["routes"].items()):
            lines.append(f'hookjudge_judgements{{route="{name}"}} {stats["count"]}')
            lines.append(f'hookjudge_latency_ms{{route="{name}"}} {stats["avg_latency_ms"]}')
        lines += [
            "# HELP hookjudge_cost_24h Model spend in the last 24h.",
            "# TYPE hookjudge_cost_24h gauge",
            f"hookjudge_cost_24h {data['cost']}",
            "# HELP hookjudge_returns Return-leg states in the last 24h.",
            "# TYPE hookjudge_returns gauge",
        ]
        for name, count in sorted(data["returns"].items()):
            lines.append(f'hookjudge_returns{{status="{name}"}} {count}')
        return "\n".join(lines) + "\n"

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    page = (Path(__file__).parent / "status.html").read_text(encoding="utf-8")

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return page

    return app
