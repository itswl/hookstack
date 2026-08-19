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
import logging
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse

from hookjudge.alarm import SelfAlarm
from hookjudge.contract import IMPORTANCE, Incoming, Outgoing
from hookjudge.judge import ai_verdict, reuse_verdict, rule_reuse_verdict, rule_verdict
from hookjudge.live import Live
from hookjudge.settings import Settings
from hookjudge.store import Store, now_ts

logger = logging.getLogger("hookjudge.app")

_BACKOFF_SECONDS = (5, 30, 120, 600, 1800)


def constant_time_eq(expected: str, provided: str | None) -> bool:
    """Compare two header-derived strings without leaking length by timing.

    Wraps hmac.compare_digest because that function raises TypeError on a
    str holding non-ASCII, and Starlette decodes header bytes as latin-1 — so
    a single 0xF6 byte in a signature or token header turned both gates here
    into an unauthenticated HTTP 500 instead of a 401. Comparing the utf-8
    bytes keeps the constant-time property and answers the way it should.
    """
    return hmac.compare_digest(expected.encode("utf-8"), (provided or "").encode("utf-8"))


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
        return constant_time_eq(expected, given)
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return constant_time_eq(expected, given)


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or Settings.load()
    store = Store(app_settings.db_path)
    live = Live()
    store.on_change = live.changed
    alarm = SelfAlarm(app_settings.alarm_url, app_settings.alarm_min_interval_seconds)

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
            verdict = rule_verdict(event, degraded_reason="recovery alerts are not analysed on their own")
        else:
            # Cheapest tier that is not a guess: this rule's own last AI verdict.
            # Measured on 795 production alerts, 28 of 29 rules answered the same
            # way every single time, so the second firing of a rule is usually a
            # question already paid for. Off unless a window is configured.
            by_rule = await store.prior_rule_verdict(
                event.rule_key, event.level, app_settings.rule_reuse_window_seconds, now_ts()
            )
            if by_rule is not None:
                verdict = rule_reuse_verdict(by_rule, event)
            else:
                verdict = await ai_verdict(client, app_settings, event)
        latency_ms = int((time.monotonic() - started) * 1000)
        return await store.record(event, verdict, latency_ms)

    async def _return_once(client: httpx.AsyncClient, row: dict[str, Any], now: float) -> None:
        """Hand one judgement back to the pipe. The only delivery this service
        does, and only to one address — fan-out is the pipe's job."""
        if app_settings.return_url.strip().lower() in ("none", "off"):
            # A ledger-only deployment (the shadow run): the verdict's whole
            # journey ends in the ledger, by declaration rather than by
            # accident — which is what separates it from the case below.
            await store.mark_return(
                int(row["id"]), "skipped", int(row["return_attempts"]), "returns disabled (ledger-only deployment)"
            )
            return
        if not app_settings.return_url:
            await store.mark_return(
                int(row["id"]), "dead", int(row["return_attempts"]), "HOOKJUDGE_RETURN_URL is not configured"
            )
            await alarm.dead_return(
                client, title=str(row["title"]), error="HOOKJUDGE_RETURN_URL is not configured", now=now
            )
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
        if status == "dead":
            await alarm.dead_return(client, title=str(row["title"]), error=error, now=now)

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
            except Exception:  # noqa: BLE001 — the loop must survive anything
                logger.exception("worker error")
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

    # One judgement in flight per identity. A storm is N restatements of one
    # condition arriving inside one model-latency window; unserialized, every
    # one of them misses the verdict the others have not written yet, and the
    # reuse route — the whole point of which is storms — only starts working
    # after the storm is over. Watched live in the demo: an identical repeat,
    # one second apart, billed a second ai judgement. The lock queues the
    # restatements behind the first call; each then re-reads the ledger and
    # reuses. Locks are refcounted away so the map cannot grow with alert
    # cardinality.
    identity_locks: dict[str, tuple[asyncio.Lock, int]] = {}

    async def _judge_serialized(client: httpx.AsyncClient, event: Incoming) -> None:
        lock, holders = identity_locks.get(event.identity, (asyncio.Lock(), 0))
        identity_locks[event.identity] = (lock, holders + 1)
        try:
            async with lock:
                await _judge_and_record(client, event)
        finally:
            lock_again, holders_now = identity_locks[event.identity]
            if holders_now <= 1:
                del identity_locks[event.identity]
            else:
                identity_locks[event.identity] = (lock_again, holders_now - 1)

    app = FastAPI(title="hookjudge", lifespan=lifespan)
    app.state.settings = app_settings
    app.state.store = store
    app.state.judge_and_record = _judge_and_record
    app.state.judge_tasks = set()

    def _read_guard(token: str | None, authorization: str | None) -> None:
        configured = app_settings.read_token
        if not configured:
            return
        bearer = authorization[7:].strip() if authorization and authorization.lower().startswith("bearer ") else None
        if not ((token and constant_time_eq(configured, token)) or (bearer and constant_time_eq(configured, bearer))):
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
        task = asyncio.create_task(_judge_serialized(app.state.client, event))
        # Held, or the garbage collector may cancel a fire-and-forget task
        # mid-judgement — the documented asyncio footgun.
        app.state.judge_tasks.add(task)
        task.add_done_callback(app.state.judge_tasks.discard)
        return JSONResponse({"accepted": True, "identity": event.identity}, status_code=202)

    @app.get("/live")
    async def live_stream(
        x_read_token: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
    ) -> StreamingResponse:
        """The board's wake-up line: one `changed` per write, a `ping` through the quiet.

        It carries no rows on purpose — the viewer has a route filter, a search
        and a window of its own, so "look again" is both smaller and more correct
        than pushing rows it may not be asking for.
        """
        _read_guard(x_read_token, authorization)
        return StreamingResponse(
            live.stream(),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

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

    @app.get("/disagreements")
    async def disagreements(
        x_read_token: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        limit: int = 50,
    ) -> dict[str, Any]:
        """The review queue: rows where the platform and the judge disagreed.

        In the shadow deployment `level` carries the platform's own verdict, so
        every row here is a labelled-comparison candidate — the operator's
        ruling turns it into an eval row, which is the cheapest labelling the
        eval set will ever get.
        """
        _read_guard(x_read_token, authorization)
        return {"queue": await store.disagreements(limit)}

    @app.post("/judgements/{judgement_id}/label")
    async def label_judgement(
        judgement_id: int,
        payload: dict[str, Any],
        x_read_token: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """Record the operator's ruling. Guarded by the read token on purpose:
        this service has exactly one operator surface and one token; a label is
        an annotation on history, not a mutation of behaviour."""
        _read_guard(x_read_token, authorization)
        importance = str(payload.get("importance") or "").strip().lower()
        if importance not in IMPORTANCE:
            raise HTTPException(status_code=400, detail=f"importance must be one of {', '.join(IMPORTANCE)}")
        source = str(payload.get("source") or "operator").strip().lower()
        if source not in ("platform", "judge", "operator"):
            raise HTTPException(status_code=400, detail="source must be platform, judge or operator")
        if not await store.set_label(judgement_id, importance, source, now_ts()):
            raise HTTPException(status_code=404, detail="judgement not found")
        return {"labeled": True, "id": judgement_id, "importance": importance, "source": source}

    @app.get("/labels/export")
    async def export_labels(
        x_read_token: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
    ) -> PlainTextResponse:
        """Every ruling as eval-harness JSONL (see eval/README.md) — pipe it
        straight into eval/data. The note keeps the provenance: what the
        platform said, what the judge said, whose answer the operator took."""
        _read_guard(x_read_token, authorization)
        lines = []
        for row in await store.labeled():
            try:
                fields = json.loads(row["fields_json"] or "{}")
            except ValueError:
                fields = {}
            lines.append(
                json.dumps(
                    {
                        "id": f"ledger-{row['id']}",
                        "seen": 1,
                        "alert": {
                            "source": str(fields.get("origin") or row["source"]),
                            "title": row["title"],
                            "body": row["body"],
                            "level": row["level"],
                            "fields": fields,
                        },
                        "expect": {"importance": row["label_importance"], "event_type": row["event_type"]},
                        "reviewed": True,
                        "note": (
                            f"ledger ruling ({row['label_source']}): platform said {row['level']}, "
                            f"judge said {row['importance']}"
                        ),
                    },
                    ensure_ascii=False,
                )
            )
        return PlainTextResponse("\n".join(lines) + ("\n" if lines else ""), media_type="application/x-ndjson")

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
