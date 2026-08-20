"""hookjudge — a brain behind a pipe. It judges; it does nothing else.

    POST /events    the pipe's normalized event. Answers 202 immediately.
    POST /feedback  a human pressed a button on a card. Answers 202.
    GET  /status    the ledger: verdicts, routes, cost, attention, return state
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
from hookjudge.contract import ACTION_KINDS, ACTION_SILENCE, ACTION_USEFUL, IMPORTANCE, Incoming, Outgoing
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


def _prom_label(value: str) -> str:
    """A Prometheus label value: backslash, quote and newline are the three
    characters the text exposition format cannot carry raw. An alert identity is
    "source|title|k=v", and a title arrives with whatever punctuation the
    monitoring stack felt like putting in it — one unescaped quote turns the
    whole scrape into a parse error, so every condition's numbers vanish rather
    than just that one's."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


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
            sent_at = float(timestamp.strip())
        except ValueError:
            return False
        if abs(now - sent_at) > 300:
            return False
        # Stripped on both lines, matching hookrelay.security.verify_signature.
        # It did not, and the pair are two doors of one family: a padded
        # timestamp header verified at the pipe and failed here, because the
        # relay signs the stripped value and this signed the padded one. No
        # sender in the family pads, so it never fired — the kind of divergence
        # that waits for a new client rather than announcing itself.
        signed = timestamp.strip().encode() + b"." + body
        expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
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
        """One event, one verdict, one row. Never raises into the caller.

        That sentence was here before anything in the code kept it. This runs as
        a fire-and-forget task whose done-callback only discarded it from a set,
        so the exception was never retrieved: one sqlite error lost the alert
        AFTER the pipe had been answered 202 — no ledger row, no log line, and
        the only trace was "Task exception was never retrieved" on stderr,
        whenever the garbage collector got round to saying it, without the alert
        identity in it. Returns the row id, or 0 when nothing was recorded.
        """
        started = time.monotonic()
        try:
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
        except Exception as exc:  # noqa: BLE001 — the promise in the docstring, kept
            # CancelledError is a BaseException, so shutdown still cancels this
            # cleanly; only a real failure lands here.
            logger.exception("alert accepted then judged nowhere: %s", event.identity)
            # And the alarm, not only the log. This is the one failure mode where
            # nobody else is holding a copy: the sender was told 202 and will not
            # retry, the ledger has no row to dead-letter, and /status cannot show
            # an absence. The dead-return alarm exists because the pipe is the
            # broken link; this exists because there IS no link left. It goes
            # through the same rate limit and cannot raise (see alarm.py).
            await alarm.lost_alert(client, title=event.title, error=f"{exc.__class__.__name__}: {exc}", now=now_ts())
            return 0

    async def _return_once(client: httpx.AsyncClient, row: dict[str, Any], now: float) -> None:
        """Hand one judgement back to the pipe. The only delivery this service
        does, and only to one address — fan-out is the pipe's job."""
        if app_settings.return_url.strip().lower() in ("none", "off"):
            # A ledger-only deployment (the shadow run): the verdict's whole
            # journey ends in the ledger, by declaration rather than by
            # accident — which is what separates it from the case below.
            await store.mark_return(
                int(row["id"]),
                "skipped",
                int(row["return_attempts"]),
                "returns disabled (ledger-only deployment)",
                attempted_at=now,
            )
            return
        if not app_settings.return_url:
            await store.mark_return(
                int(row["id"]),
                "dead",
                int(row["return_attempts"]),
                "HOOKJUDGE_RETURN_URL is not configured",
                attempted_at=now,
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
            correlation_id=str(row["correlation_id"] or ""),
            received_at=float(row["received_at"]),
            # The stored fact, not a re-sniff of the stored title. It was only
            # ever needed for meta.is_recovery, which was patched onto the
            # payload below — but the declared ACTIONS now turn on it too, and
            # an Alertmanager recovery whose body reuses the firing's summary
            # contains no recovery word to sniff. Re-deriving it here would have
            # put "Silence 4h" on a card about something already over.
            recovery_flag=bool(row["is_recovery"]),
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
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
        headers = {"content-type": "application/json"}
        if app_settings.return_secret:
            stamp = str(int(now))
            headers["X-Hook-Timestamp"] = stamp
            headers["X-Hook-Signature"] = hmac.new(
                app_settings.return_secret.encode(), stamp.encode() + b"." + body, hashlib.sha256
            ).hexdigest()
        attempts = int(row["return_attempts"]) + 1
        # Every exit below advances the attempt count, and the delivery is the
        # only thing inside the try. It used to catch httpx.HTTPError alone, with
        # the "sent" write inside it too, so anything else — a TypeError from a
        # stub client, an ssl error httpx does not wrap, a sqlite failure on the
        # way out — escaped to the worker's catch-all. That abandoned the rest of
        # the tick AND left the row `queued` with return_attempts unchanged, so
        # it never backed off and never died; pending_returns is ORDER BY id
        # LIMIT 50, so one such row holds a queue slot forever and fifty of them
        # starve every judgement behind them.
        error: str | None = None
        try:
            response = await client.post(app_settings.return_url, content=body, headers=headers, timeout=10.0)
            if response.status_code >= 300:
                error = f"http {response.status_code}: {response.text[:200]}"
        except httpx.HTTPError as exc:
            error = f"transport: {exc.__class__.__name__}"
        except Exception as exc:  # noqa: BLE001 — a failure we cannot name is still a failed attempt
            # Named by class and message rather than called "transport", because
            # a reader debugging a dead letter has only this string, and telling
            # them the network broke when sqlite did costs them the afternoon.
            error = f"{exc.__class__.__name__}: {exc}"[:200]
        if error is None:
            await store.mark_return(int(row["id"]), "sent", attempts, None, attempted_at=now)
            return
        status = "queued" if attempts < app_settings.return_max_attempts else "dead"
        await store.mark_return(int(row["id"]), status, attempts, error, attempted_at=now)
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
                    # retried patiently rather than hammered. Measured from the
                    # last ATTEMPT: it read received_at, which is when the alert
                    # arrived, so an hour-old row satisfied every wait in the
                    # table instantly and was re-posted on every tick — patient
                    # for the first ten minutes of a row's life and a hammer
                    # after that, which is backwards. return_attempted_at is
                    # NULL on rows queued before that column existed, and those
                    # fall back to arrival.
                    wait = _BACKOFF_SECONDS[min(int(row["return_attempts"]), len(_BACKOFF_SECONDS) - 1)]
                    last = row["return_attempted_at"]
                    since = float(last) if last is not None else float(row["received_at"])
                    if int(row["return_attempts"]) and now - since < wait:
                        continue
                    try:
                        await _return_once(client, row, now)
                    except Exception:  # noqa: BLE001 — one row's failure is not the tick's
                        # _return_once handles a failed DELIVERY itself; this is
                        # for a row whose own bookkeeping cannot be written at
                        # all. Rethrowing here would abandon every row after it
                        # in the same tick, forever, because the queue is ordered
                        # by id and the broken row is always first again.
                        logger.exception("return leg failed for judgement %s", row["id"])
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

    def _judge_task_done(task: asyncio.Task[None]) -> None:
        """Release the task and RETRIEVE its exception.

        The callback used to be `set.discard` alone, which never touches
        task.exception() — so anything escaping the judge became asyncio's
        "Task exception was never retrieved" on stderr at collection time, with
        no alert identity in it and no timestamp anyone could line up with a
        request. _judge_and_record now guards the judging itself; this is the
        backstop for what is left around it — the lock bookkeeping above, and
        any future caller of this task — so that no path can lose an alert
        silently again.
        """
        app.state.judge_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error("judge task failed outside the guard: %r", exc)

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

    def _write_guard(token: str | None, authorization: str | None) -> None:
        """The same token as the read side, the opposite answer when it is unset.

        Reads staying open with no token configured is deliberate across this
        family — /status on a laptop should not need a credential — and that
        stays. A mutating endpoint cannot borrow the rule: _read_guard returns
        early on an empty token, so an unconfigured instance let whoever found
        the port rewrite the operator labels the eval set is built from and
        download every alert body in the ledger, and the more locked-down the
        deployment (no token set because nothing was meant to be exposed) the
        wider these two doors stood open. hookrelay settled this for the family;
        its security.py token_ok says it outright — "dev mode for read, endpoint
        disabled for admin — the CALLER decides which semantic applies". This is
        the caller that decides the second way.

        403 rather than 401: 401 invites a credential, and there is no credential
        that opens this. The endpoint is off until one is configured.
        """
        if not app_settings.read_token:
            raise HTTPException(status_code=403, detail="endpoint disabled: set HOOKJUDGE_READ_TOKEN to enable it")
        _read_guard(token, authorization)

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
        task.add_done_callback(_judge_task_done)
        return JSONResponse({"accepted": True, "identity": event.identity}, status_code=202)

    @app.post("/feedback")
    async def feedback(
        request: Request,
        x_hook_signature: str | None = Header(default=None),
        x_hook_timestamp: str | None = Header(default=None),
    ) -> JSONResponse:
        """A human pressed a button on a card, and the pipe brought the press here.

        Signed with the SAME timestamped HMAC and the same secret as /events,
        because it arrives from the same sender through the same edge. hookrelay
        owns the callback and the token behind the button; what reaches this door
        is the ruling with the channel already stripped off it — an opaque actor
        id at most. The judge still cannot name a channel or hold a secret.

        `useful` says being interrupted was worth it, `useless` says it was not.
        `silence` is answered but NOT recorded as a ruling: "make it stop" is not
        "it did not matter" — an operator silences a real incident they are
        already working on, and counting that as noise would corrupt the one
        number this door exists to keep honest. Nothing here suppresses anything;
        whether a reused verdict should stop producing cards is a decision that
        remains open, and measuring it is not deciding it.
        """
        body = await request.body()
        if len(body) > app_settings.max_body_bytes:
            raise HTTPException(status_code=413, detail="body too large")
        if not verify_signature(app_settings.ingest_secret, body, x_hook_signature, x_hook_timestamp, now_ts()):
            raise HTTPException(status_code=401, detail="bad signature")
        try:
            parsed = json.loads(body or b"{}")
        except ValueError:
            raise HTTPException(status_code=400, detail="body is not JSON") from None
        payload = parsed if isinstance(parsed, dict) else {}
        action = payload.get("action")
        if not isinstance(action, dict):
            action = {}
        kind = str(action.get("kind") or "").strip().lower()
        if kind not in ACTION_KINDS:
            raise HTTPException(status_code=400, detail=f"kind must be one of {', '.join(ACTION_KINDS)}")
        correlation_id = str(payload.get("correlation_id") or "").strip()
        event_id = str(payload.get("event_id") or "").strip()
        try:
            at = float(payload.get("at") or now_ts())
        except (TypeError, ValueError):
            at = now_ts()
        if kind == ACTION_SILENCE:
            # Named in the answer rather than swallowed. A door that returns 202
            # for something it did nothing about is the same lie as a verdict
            # that hides its own downgrade.
            logger.info("silence pressed on %s; not a ruling, and the judge suppresses nothing", correlation_id)
            return JSONResponse(
                {"recorded": False, "reason": "silence is a request to the pipe, not a ruling on whether it mattered"},
                status_code=202,
            )
        ruling = await store.record_mattered(
            correlation_id,
            mattered="yes" if kind == ACTION_USEFUL else "no",
            at=at,
            actor=str(payload.get("actor") or ""),
            event_id=event_id,
        )
        if ruling is None:
            # 202 with the reason named, not 404: a retry cannot conjure the
            # judgement, and a pipe that reads this as a failure would redeliver
            # a press nobody can file forever.
            return JSONResponse(
                {"recorded": False, "reason": "no judgement carries that correlation id"}, status_code=202
            )
        return JSONResponse({"recorded": True, **ruling}, status_code=202)

    @app.post("/rulings/ai")
    async def ai_ruling(
        request: Request,
        x_hook_signature: str | None = Header(default=None),
        x_hook_timestamp: str | None = Header(default=None),
    ) -> JSONResponse:
        """A model's retrospective ruling on a CONDITION, from the investigator.

        A separate door from /feedback, not a flag on it, and that is the whole
        design. `mattered` is the only field in this ledger that means a person
        said so; `ruled` counts it and `mattered_pct` divides by it. A boolean on
        the human door deciding which column a write lands in is one bug away
        from making all three of those numbers untraceable. Two doors cannot be
        confused by a typo.

        What justifies automating this one and not the memory gate: the failure
        mode. A wrong ruling here is a wrong number in a ledger — visible,
        overwritable, and it compounds into nothing. A wrong memory acceptance is
        a line in CLAUDE.md loaded as instruction by every later run, proposed by
        a model that read attacker-influenced alert text. Same actor, different
        blast radius, so different rules. See
        .agents/notes/implemented/2026-08-20-the-signal-that-needs-no-human.md.

        Signed with the ingest secret, like every other door here: the caller is
        hookprobe, over the private network, and the judge still holds no
        knowledge of channels or people.
        """
        body = await request.body()
        if len(body) > app_settings.max_body_bytes:
            raise HTTPException(status_code=413, detail="body too large")
        # Closed, not open, when unconfigured. Everywhere else here an empty
        # secret means "an in-network hop between two containers of one
        # deployment" and verify_signature waves it through. Not on this door:
        # its caller is the investigator, the one component that reads
        # attacker-influenced text, and a ledger-write door that authenticates
        # nobody is worse than a feature that is switched off.
        if not app_settings.ruling_secret:
            raise HTTPException(status_code=503, detail="set HOOKJUDGE_RULING_SECRET to open this door")
        if not verify_signature(app_settings.ruling_secret, body, x_hook_signature, x_hook_timestamp, now_ts()):
            raise HTTPException(status_code=401, detail="bad signature")
        try:
            parsed = json.loads(body or b"{}")
        except ValueError:
            raise HTTPException(status_code=400, detail="body is not JSON") from None
        payload = parsed if isinstance(parsed, dict) else {}
        identity = str(payload.get("identity") or "").strip()
        if not identity:
            # Checked BEFORE the write, which it was not: an empty identity is
            # a valid primary key, so the row landed and was rejected afterwards.
            raise HTTPException(status_code=400, detail="identity must not be empty")
        try:
            recorded = await store.record_ai_ruling(
                identity,
                verdict=str(payload.get("verdict") or "").strip(),
                why=str(payload.get("why") or ""),
                model=str(payload.get("model") or ""),
                at=now_ts(),
            )
        except ValueError as exc:
            # 400, not 202: an unknown verdict or a missing reason is a caller
            # that has to be fixed, and a retry of the same body cannot help.
            raise HTTPException(status_code=400, detail=str(exc)) from None
        return JSONResponse({"recorded": True, **recorded}, status_code=202)

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
        """Record the operator's ruling. The read token on purpose: this service
        has exactly one operator surface and one token, and a label is an
        annotation on history, not a mutation of behaviour. But it is still a
        WRITE, so an empty token disables it rather than opening it — see
        _write_guard."""
        _write_guard(x_read_token, authorization)
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
        platform said, what the judge said, whose answer the operator took.

        A GET, but held to the write rule: one request hands over the full body
        of every labelled alert, which is the most sensitive payload this service
        holds. A bulk export with nothing configured to protect it disables
        itself — see _write_guard.
        """
        _write_guard(x_read_token, authorization)
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
        attention = data["attention"]
        lines += [
            "# HELP hookjudge_interruptions Cards a human received in the last 24h — every judgement becomes one.",
            "# TYPE hookjudge_interruptions gauge",
            f"hookjudge_interruptions {attention['interruptions']}",
            "# HELP hookjudge_conditions Distinct conditions behind those interruptions.",
            "# TYPE hookjudge_conditions gauge",
            f"hookjudge_conditions {attention['conditions']}",
            "# HELP hookjudge_repeat_interruptions Interruptions restating a condition already reported.",
            # The unattended signal, beside the one that needs a human. When
            # `attention_rulings` stays flat at zero — the normal case on a
            # deployment nobody watches — this is the line that still moves.
            "# TYPE hookjudge_likely_flapping gauge",
            f"hookjudge_likely_flapping {attention.get('likely_flapping') or 0}",
            "# HELP hookjudge_ai_rulings Conditions a model ruled on retrospectively. Not `attention_rulings`.",
            "# TYPE hookjudge_ai_rulings gauge",
            f"hookjudge_ai_rulings {attention.get('ai_ruled') or 0}",
            f"hookjudge_ai_not_worth_it {attention.get('ai_not_worth_it') or 0}",
            "# TYPE hookjudge_repeat_interruptions gauge",
            f"hookjudge_repeat_interruptions {attention['repeats']}",
            "# HELP hookjudge_attention_rulings Human rulings on whether an interruption was worth it.",
            "# TYPE hookjudge_attention_rulings gauge",
            f'hookjudge_attention_rulings{{ruling="mattered"}} {attention["mattered"]}',
            f'hookjudge_attention_rulings{{ruling="did_not_matter"}} {attention["did_not_matter"]}',
            "# HELP hookjudge_condition_interruptions Interruptions per condition, noisiest first.",
            "# TYPE hookjudge_condition_interruptions gauge",
        ]
        # Only the noisiest few carry a per-condition label. An alert identity as
        # a label value is unbounded cardinality, which is how a metrics store
        # gets taken down by the very storm it was meant to describe; the store
        # caps the list, and that cap is what makes these safe to emit.
        for condition in attention["noisiest"]:
            label = _prom_label(str(condition["identity"]))
            lines.append(f'hookjudge_condition_interruptions{{condition="{label}"}} {condition["interruptions"]}')
            lines.append(f'hookjudge_condition_mattered{{condition="{label}"}} {condition["mattered"]}')
        return "\n".join(lines) + "\n"

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    page = (Path(__file__).parent / "status.html").read_text(encoding="utf-8")

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return page

    return app
