"""HookRelay — receive webhooks, decide, fan out. The FastAPI wiring.

Endpoints:
    POST /hook/{source}   inbound door (per-source HMAC)
    GET  /status          queue health + recent decisions (read token)
    POST /silences        quiet a source or everything (admin token)
    DELETE /silences/{id}
    GET  /healthz
"""

from __future__ import annotations

import asyncio
import contextlib
import html
import json
import logging
import os
import re
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse

from hookrelay import actions, metrics, registry
from hookrelay.alarm import SelfAlarm
from hookrelay.breaker import CircuitBreaker
from hookrelay.config import CardAction, Config, ConfigError, _warn_posture_mix
from hookrelay.delivery import process_due
from hookrelay.fuse import StormFuse
from hookrelay.live import Live
from hookrelay.pipeline import handle_hook, record_storm_suppressed
from hookrelay.security import token_ok, verify_signature
from hookrelay.settings import Settings
from hookrelay.store import Store, now_ts
from hookrelay.timeline import render as render_timeline
from hookrelay.topology import render as render_topology

# A `secret:` whose value is written out rather than referenced. Empty values and
# ${REFS} are excluded in the pattern itself, not filtered afterwards, so the one
# thing this must never do — mask an unsigned door into looking like a signed one
# — cannot happen through a later edit to the replacement.
# `[ \t]*`, never `\s*`: under re.MULTILINE, `\s` matches a newline, so a `\s*`
# prefix lets a match START on an earlier line and slide past the lookaheads
# below — which it did, masking both `${REF}` and `""` on the first attempt.
_INLINE_SECRET = re.compile(
    # Each lookahead tolerates leading whitespace ITSELF, because the `[ \t]*`
    # in the prefix can backtrack to zero — which let the check run one space
    # early and miss both `${` and `""`, masking the two things it must not.
    r"^([ \t]*(?:-[ \t]+)?secret:[ \t]*)"
    r"(?![ \t]*\$\{)"  # a reference: the name is the useful part, the value is elsewhere
    r"(?![ \t]*$)"  # no value at all
    r"""(?![ \t]*(?:""|'')[ \t]*$)"""  # an explicitly EMPTY secret = an unsigned door, a fact to show
    r".+?[ \t]*$",
    re.MULTILINE,
)


def _redact_secrets(text: str) -> str:
    """Mask inline `secret:` values, leaving the file otherwise byte-identical.

    A regex over the raw text rather than a YAML round-trip on purpose: the
    configs in this family carry their reasoning in comments, and a re-emit
    would drop every one of them to redact a line that is usually `${NAME}`
    anyway.
    """
    return _INLINE_SECRET.sub(lambda m: f"{m.group(1)}<redacted>", text)


logger = logging.getLogger("hookrelay.app")

# The synthetic source a card press is recorded under. Named rather than blank
# so it is obvious in the ledger that a human, not an upstream, made this event.
_ACTION_SOURCE = "card-action"


def _card_token(payload: dict[str, Any]) -> str:
    """Find our token in whatever envelope the IM platform wrapped it in.

    Feishu posts the button's `value` under action.value; a plain caller may
    post it at the top level. Both, rather than a per-platform parser: the
    token is what carries the authority, so where it was nested is a detail.
    """
    for candidate in (payload, payload.get("action"), payload.get("event"), payload.get("value")):
        if isinstance(candidate, dict):
            nested = candidate.get("value")
            if isinstance(nested, dict) and nested.get("hookrelay_action"):
                return str(nested["hookrelay_action"])
            if candidate.get("hookrelay_action"):
                return str(candidate["hookrelay_action"])
    return ""


def _im_actor(payload: dict[str, Any]) -> str:
    """An opaque user id if the platform sent one — never a display name.

    Who pressed it belongs in the timeline; who they are does not belong in this
    service's ledger.
    """
    for key in ("open_id", "user_id", "operator_id", "senderId"):
        for holder in (payload, payload.get("event"), payload.get("operator")):
            if isinstance(holder, dict) and holder.get(key):
                return str(holder[key])
    return ""


async def _capped_body(request: Request, limit: int) -> bytes:
    """Read the body, refusing anything over `limit` — cheaply where possible.

    `await request.body()` buffers the WHOLE request before anyone can object,
    so a length check after it means a 1 GB POST is paid for in memory and only
    then refused: the door was open the entire time it was being filled.
    Content-Length is the cheap first gate. It is caller-supplied, so it can lie
    or be absent (chunked encoding sends none), which is exactly why the
    post-read check stays — two gates, one answer.
    """
    declared = request.headers.get("content-length")
    if declared is not None and declared.strip().isdigit() and int(declared) > limit:
        raise HTTPException(status_code=413, detail="body too large")
    body = await request.body()
    if len(body) > limit:
        raise HTTPException(status_code=413, detail="body too large")
    return body


def _escalation_can_work(settings: Settings, cfg: Config) -> bool:
    """Can a human press anything at all in this deployment?

    Three things have to line up, and if any is missing the escalation sweep is
    left disarmed with a reason in the log rather than firing on every alert:

      a secret        — without it no card carries an action of any kind.
      an enabled kind — card_actions decides what is offered; empty offers none.
      a channel that can carry one — `feishu` posts a callback, and the markdown
        dialects carry a LINK, which needs public_url to point anywhere.

    KNOWN LIMIT, stated because it is a real one: this is a per-DEPLOYMENT
    answer, not a per-alert one. A deployment whose critical route reaches only a
    plain `generic` webhook, while some other route reaches Feishu, passes this
    check and will still escalate those critical alerts as untouched. Answering
    per alert means asking which channels each event was delivered to — a query
    per event on a loop that runs every second — and that cost is not worth
    paying for a mixed setup nobody has yet reported having.
    """
    if not settings.action_secret or not cfg.card_actions:
        return False
    for channel in cfg.channels.values():
        if channel.type == "feishu":
            return True
        if channel.type in ("dingtalk", "wecom") and settings.public_url:
            return True
    return False


def create_app(settings: Settings | None = None, cfg: Config | None = None) -> FastAPI:
    """App factory: tests hand in Settings/Config directly; production loads
    them from the environment and config.yaml."""
    app_settings = settings or Settings.load()
    # Plugins load BEFORE config validation: config references adapters,
    # processors and channel types by name, and unknown names must fail the
    # boot, not the first event.
    loaded_plugins = registry.load_plugins(app_settings.plugins_dir)
    if loaded_plugins:
        logger.info("plugins loaded: %s", ", ".join(loaded_plugins))
    app_config = cfg or Config.from_file(app_settings.config_path)
    # The doctrine's own asterisk, said out loud once at boot. It had lived only
    # in README prose, so a pipeline could run dedup in front of a brain forever
    # and nothing would mention it.
    posture_warning = _warn_posture_mix(app_config.pipeline, app_config.channels)
    if posture_warning:
        logger.warning("%s", posture_warning)
    # Buttons that cannot be signed are buttons that do nothing, and until now
    # that was silent. `_mint_card_actions` returns early without a secret, so
    # every card shipped with `value: {}` — it RENDERED, the label was right, and
    # a press carried nothing back. On this deployment that had been true since
    # the feature landed, which is most of the reason `ruled` sat at 0 while I
    # was reading it as "nobody answers".
    #
    # Not fatal: a verdict must still reach its channel. But an operator who
    # wrote `card_actions` into their config asked for working buttons, and the
    # gap between that and what ships has to be said out loud.
    if app_config.card_actions and not app_settings.action_secret:
        logger.error(
            "card_actions configures %s but HOOKRELAY_ACTION_SECRET is empty: "
            "every button will render and do nothing, because there is no key to sign the token in it",
            ", ".join(sorted(app_config.card_actions)),
        )
    store = Store(app_settings.db_path)
    live = Live()
    store.on_change = live.changed

    async def _worker_loop(client: httpx.AsyncClient) -> None:
        next_purge = 0.0
        while True:
            try:
                now = now_ts()
                await process_due(
                    store,
                    app.state.config,
                    app_settings,
                    client,
                    now,
                    alarm=app.state.alarm,
                    breaker=app.state.breaker,
                )
                # Nobody was awake. The family judges an alert well, dresses it
                # well and delivers it well, and then has no answer for the case
                # where it lands in a channel at 3am and no human touches it.
                # This is that answer, and it deliberately needs no identity
                # model: the card_actions ledger says whether ANY person acted.
                await _escalate_cold(now)
                # Retention rides the same loop, hourly: once ALL traffic
                # passes through this ledger it must not grow forever.
                if app_settings.retention_days > 0 and now >= next_purge:
                    next_purge = now + 3600
                    purged = await store.purge_older_than(now - app_settings.retention_days * 86400, now)
                    if purged["events"] or purged["silences"]:
                        logger.info("retention: purged %d events, %d silences", purged["events"], purged["silences"])
            except Exception:  # noqa: BLE001 — the loop must survive anything
                logger.exception("worker error")
            await asyncio.sleep(app_settings.worker_interval_seconds)

    async def _escalate_cold(now: float) -> None:
        """Re-deliver alerts that went cold with nobody touching them.

        Enqueued as ordinary deliveries against the SAME event, so the second
        attempt inherits the retry, the rate limit, the dead letter and the
        ledger row — and reads on the board as what it is: this alert, sent
        somewhere else, later. No new event, because a second event about the
        first one is how a ledger starts lying about how many alerts arrived.

        The stamp is taken BEFORE the deliveries are enqueued and only one
        caller can win it, so a crash between the two costs one escalation
        rather than an escalation every tick forever.
        """
        rule = app.state.config.escalation
        if rule is None or not app.state.escalation_armed:
            return
        cold = await app.state.store.cold_events(before=now - rule.after_minutes * 60, levels=rule.levels, limit=50)
        for event in cold:
            if not await app.state.store.mark_escalated(int(event["id"]), now):
                continue  # another tick got there first
            for channel in rule.send_to:
                await app.state.store.enqueue_delivery(int(event["id"]), channel, now)
            logger.info(
                "escalated event #%s (%s) to %s — untouched for %d minutes",
                event["id"],
                event["level"],
                ", ".join(rule.send_to),
                rule.after_minutes,
            )

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await store.open()
        client = httpx.AsyncClient(timeout=10.0)
        app.state.http_client = client
        worker = asyncio.create_task(_worker_loop(client))
        try:
            yield
        finally:
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker
            await client.aclose()
            await store.close()

    app = FastAPI(title="hookrelay", lifespan=lifespan)
    app.state.settings = app_settings
    app.state.config = app_config
    app.state.store = store
    # The fuse sits in the fusebox, not in application logic: process-local
    # counters, applied at the door regardless of what the pipeline says.
    app.state.fuse = StormFuse()
    app.state.alarm = SelfAlarm(app_settings.alarm_url, app_settings.alarm_min_interval_seconds)
    app.state.breaker = CircuitBreaker(
        threshold=app_settings.breaker_threshold, cooldown_seconds=app_settings.breaker_cooldown_seconds
    )
    # Escalation asks "did any human touch this?", and the only evidence of that
    # is a card action press. So on a deployment where no press can EVER happen
    # it would read every alert as ignored and escalate all of them — turning a
    # feature meant to catch the one alert nobody saw into a second copy of every
    # alert. Decided once here rather than per event, because the answer cannot
    # change without a restart and this loop runs every second.
    app.state.escalation_armed = _escalation_can_work(app_settings, app_config)
    if app_config.escalation is not None and not app.state.escalation_armed:
        logger.warning(
            "escalation is configured but disarmed: no card action can be pressed in this deployment "
            "(needs HOOKRELAY_ACTION_SECRET, a card_actions kind, and either a feishu channel or "
            "HOOKRELAY_PUBLIC_URL for a dingtalk/wecom link). Every alert would look untouched."
        )

    # ── the page ──────────────────────────────────────────────────────────
    # One self-contained file, no build step, no CDN. The page itself is a
    # static shell; the DATA sits behind /status and its read token — so
    # serving the shell unauthenticated leaks nothing.
    status_page = (Path(__file__).parent / "status.html").read_text(encoding="utf-8")

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        """The operator board — every page this service serves hangs off it."""
        return status_page

    # ── inbound ───────────────────────────────────────────────────────────

    @app.post("/hook/{source_name}")
    async def hook(source_name: str, request: Request) -> JSONResponse:
        """The front door: one source's webhook, walked through the gate pipeline and routed."""
        source = app.state.config.sources.get(source_name)
        if source is None:
            raise HTTPException(status_code=404, detail="unknown source")
        body = await _capped_body(request, app_settings.max_body_bytes)
        # The source's ADAPTER owns both verification and payload reading —
        # GitHub's header scheme and Grafana's are one plugin apart.
        adapter = registry.SOURCE_ADAPTERS[source.adapter]
        headers = {key.lower(): value for key, value in request.headers.items()}
        if not adapter.verify(source, body, headers):
            raise HTTPException(status_code=401, detail="bad signature")
        try:
            payload = json.loads(body or b"{}")
        except ValueError:
            raise HTTPException(status_code=400, detail="body is not JSON") from None
        # ── storm fuse (volume, not content) ──────────────────────────────
        # After verification (an unsigned flood is already 401 and free) and
        # before the pipeline. Two stages: suppress keeps the account, reject
        # protects the account itself.
        verdict = app.state.fuse.check(source.name, source.storm_threshold, source.storm_window_seconds, now_ts())
        if verdict == "reject":
            raise HTTPException(status_code=429, detail="storm fuse open (hard)")
        # The adapter reads the payload for BOTH paths from here on. A suppressed
        # event is still recorded, so it must be recorded the way this door
        # actually reads its senders: the storm path used to re-extract with the
        # door's templates alone, and a reshaping adapter (SNS hides the alert
        # inside a JSON-string `Message`) mis-titled every row the storm made.
        # It sits after the hard reject, which stores nothing and so reads nothing.
        extracted = adapter.parse(source, payload)
        if verdict == "suppress":
            event_id = await record_storm_suppressed(
                app.state.store,
                source,
                payload,
                now_ts(),
                app.state.fuse.window_count(source.name),
                source.storm_threshold,
                extracted=extracted,
            )
            return JSONResponse({"event_id": event_id, "outcome": "skipped", "skip_code": "storm_suppressed"})

        result = await handle_hook(
            app.state.store,
            app.state.config,
            source,
            payload,
            now_ts(),
            settings=app_settings,
            client=app.state.http_client,
            extracted=extracted,
        )
        return JSONResponse(result)

    @app.post("/explain/{source_name}")
    async def explain(
        source_name: str, request: Request, x_admin_token: str | None = Header(default=None)
    ) -> JSONResponse:
        """Dry run: what WOULD this payload do? Admin-gated because it reveals
        routing, and because the config page is its consumer.

        Signature verification is skipped ON PURPOSE — you are asking about a
        payload you are holding, not delivering one. Nothing is recorded and
        nothing is enqueued, so the explain button can never become a way to put
        a message in the group; and every stage is TOLD it is a dry run
        (Runtime.dry_run), because the `http` stage used to POST to the
        configured brain on the way past — a "side-effect-free" answer that
        reached the network and handed a real payload to a real service. It
        reports the call it would have made instead."""
        _admin_guard(x_admin_token)
        source = app.state.config.sources.get(source_name)
        if source is None:
            raise HTTPException(status_code=404, detail="unknown source")
        try:
            payload = json.loads(await request.body() or b"{}")
        except ValueError:
            raise HTTPException(status_code=400, detail="body is not JSON") from None
        adapter = registry.SOURCE_ADAPTERS[source.adapter]
        result = await handle_hook(
            app.state.store,
            app.state.config,
            source,
            payload,
            now_ts(),
            settings=app_settings,
            client=app.state.http_client,
            extracted=adapter.parse(source, payload),
            dry_run=True,
        )
        return JSONResponse(result)

    @app.get("/topology")
    async def topology(x_admin_token: str | None = Header(default=None)) -> JSONResponse:
        """The whole shape, from config alone — the read that belongs BEFORE a
        route change rather than after one.

        `/explain` answers "where does THIS event go"; this answers "what is my
        graph", which is the question somebody composing nodes actually has. It
        names the structural hazards it can see: a door no route can match, an
        exit no route feeds, and a door that can fall through to a wildcard —
        the loop every config in this family guards against by hand.

        Admin-gated for the same reason /explain is: it reveals routing. It
        REPORTS and never refuses — a topology check that could block a reload
        would be a gate nobody can iterate a new orchestration behind, and the
        hazard it names is legitimate on a front door."""
        _admin_guard(x_admin_token)
        return JSONResponse(render_topology(app.state.config))

    # ── the card's way back ───────────────────────────────────────────────

    @app.get("/card-action", response_class=HTMLResponse)
    async def card_action_confirm(t: str = "") -> str:
        """Ask before doing. This GET performs nothing.

        DingTalk and WeCom webhook robots cannot call back, so their actions are
        LINKS — and a link in a chat message gets fetched by the client to build
        a preview. A GET that silenced an alert would therefore fire when the
        card was rendered rather than when a person decided, which is the worst
        kind of bug: an alert quietly muted by nobody.

        So the link lands here and this page does one thing — a form whose POST
        is the real action. Deliberately not a designed page: this service serves
        one board and this is not it, and an operator standing in a corridor
        wants a button, not a layout.
        """
        token = t.strip()
        if not token:
            return "<!doctype html><meta charset=utf-8><p>This link is missing its action."
        # The token is NOT verified here on purpose — verifying it would mean
        # reporting whether it is valid to anyone who fetches the URL, and a
        # preview fetch is not a caller worth answering. The POST verifies.
        safe = html.escape(token, quote=True)
        return (
            "<!doctype html><meta charset=utf-8>"
            "<title>hookrelay</title>"
            "<style>body{font:16px/1.5 system-ui;margin:3rem auto;max-width:22rem;text-align:center}"
            "button{font:inherit;padding:.6rem 1.4rem;cursor:pointer}</style>"
            "<p>Confirm this action on the alert it came from.</p>"
            f'<form method="post" action="/card-action?t={safe}"><button>Confirm</button></form>'
            "<p><small>Nothing has happened yet. This link works once.</small></p>"
        )

    @app.post("/card-action")
    async def card_action(request: Request) -> JSONResponse:
        """A human pressed a button on a notification card.

        This is the return leg the cards never had: a report arrived in chat and
        every useful response to it — quiet this, ask the investigator, approve
        that fix — lived behind a web board and a token. So nobody used them.

        WHAT AUTHORISES THIS. The token in the button, minted by this service
        (hookrelay/actions.py), signed, short-lived and single-use. Not the
        caller's identity: an IM platform's callback arrives as whatever that
        platform is, and pinning a scheme per platform is how this would grow
        four verifiers. When HOOKRELAY_CARD_CALLBACK_SECRET is set the ordinary
        family signature is also required, which is defence in depth for anyone
        who can put a gateway in front; the token is the control that always
        applies.

        Claim BEFORE acting, always: the actions on the far side of this door
        spend money and restart services, so a double press must lose the race
        rather than be handled twice.
        """
        body = await _capped_body(request, app_settings.max_body_bytes)
        callback_secret = app_settings.card_callback_secret
        if callback_secret and not verify_signature(
            callback_secret,
            body,
            request.headers.get("x-hook-signature"),
            timestamp_value=request.headers.get("x-hook-timestamp"),
            now=now_ts(),
            require_timestamp=True,
        ):
            raise HTTPException(status_code=401, detail="bad callback signature")
        try:
            payload = json.loads(body or b"{}")
        except ValueError:
            raise HTTPException(status_code=400, detail="body is not JSON") from None
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="body is not an object")

        # Two shapes reach here: an IM platform's JSON callback (Feishu), and the
        # confirm form above, which is a plain HTML POST carrying the token in
        # the query string because a form has no JSON body to put it in.
        token = _card_token(payload) or str(request.query_params.get("t") or "").strip()
        if not token:
            raise HTTPException(status_code=400, detail="no hookrelay_action in the callback value")
        now = now_ts()
        try:
            claims = actions.verify(app_settings.action_secret, token, now=now)
        except actions.ActionError as error:
            # The reason is for our log; the caller learns only that it failed.
            logger.warning("card action refused: %s", error)
            raise HTTPException(status_code=401, detail="action token refused") from None

        kind = str(claims["k"])
        configured = app.state.config.card_actions.get(kind)
        if configured is None:
            # Enabled when the button was minted, since gone. Refuse rather than
            # act on a capability this deployment has withdrawn.
            raise HTTPException(status_code=409, detail=f"action {kind!r} is no longer offered")

        correlation_id = str(claims.get("c") or "")
        event_id = int(claims.get("e") or 0) or None
        actor = str(payload.get("actor") or _im_actor(payload))
        if not await app.state.store.spend_action(
            str(claims["j"]),
            kind=kind,
            event_id=event_id,
            correlation_id=correlation_id,
            actor=actor,
            now=now,
        ):
            # Not an error: the human pressed twice, or the platform retried.
            return JSONResponse({"outcome": "already_done", "kind": kind}, status_code=200)

        outcome = await _dispatch_action(kind, configured, claims, correlation_id, event_id, actor, now)
        await app.state.store.record_action_outcome(str(claims["j"]), outcome)
        return JSONResponse({"outcome": outcome, "kind": kind, "correlation_id": correlation_id})

    async def _dispatch_action(
        kind: str,
        configured: CardAction,
        claims: dict[str, Any],
        correlation_id: str,
        event_id: int | None,
        actor: str,
        now: float,
    ) -> str:
        """Silence is ours; everything else rides the outbox to whoever owns it."""
        raw_params = claims.get("p")
        params: dict[str, Any] = raw_params if isinstance(raw_params, dict) else {}
        if kind == "silence":
            minutes = int(params.get("minutes") or 60)
            minutes = max(1, min(minutes, 7 * 24 * 60))
            source = str(params.get("source") or "*")
            if source != "*" and source not in app.state.config.sources:
                source = "*"
            await app.state.store.add_silence(
                source, now + minutes * 60, f"silenced from a card by {actor or 'an operator'}", now
            )
            return f"silenced {source} for {minutes}m"

        # An action press becomes an EVENT and a delivery, so forwarding it
        # inherits the retry, the rate limit, the dead letter and the ledger row
        # rather than becoming a second, thinner delivery mechanism.
        envelope = {
            "action": {"kind": kind, "params": params},
            "correlation_id": correlation_id,
            "event_id": event_id,
            "actor": actor,
            "at": int(now),
        }
        extracted = {
            "title": f"card action: {kind}",
            "body": f"{actor or 'an operator'} pressed {kind} on {correlation_id or f'event #{event_id}'}",
            "level": "info",
            "fields": {"kind": kind, "actor": actor},
        }
        action_event_id = await app.state.store.insert_event(
            _ACTION_SOURCE,
            f"card-action:{claims['j']}",
            extracted,
            json.dumps(envelope, ensure_ascii=False),
            now,
            correlation_id=correlation_id or None,
        )
        await app.state.store.enqueue_delivery(action_event_id, configured.forward_to, now)
        return f"forwarded {kind} to {configured.forward_to}"

    # ── read side ─────────────────────────────────────────────────────────

    def _read_guard(token: str | None, authorization: str | None = None) -> None:
        """X-Read-Token, or the standard Authorization: Bearer form.

        Both accepted because tooling speaks Bearer: Prometheus can carry a
        scrape credential from a FILE that way (its config has no env-var
        expansion), which keeps the token out of any config file."""
        configured = app.state.settings.read_token
        if not configured:
            return
        bearer = None
        if authorization and authorization.lower().startswith("bearer "):
            bearer = authorization[7:].strip()
        if not (token_ok(configured, token) or (bearer and token_ok(configured, bearer))):
            raise HTTPException(status_code=401, detail="read token required")

    @app.get("/live")
    async def live_stream(
        x_read_token: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
    ) -> StreamingResponse:
        """The ledger's wake-up line: one `changed` per write, a `ping` through the quiet.

        No rows: this board carries a source filter, an outcome filter, a search
        and a cursor, so "look again" is smaller than pushing rows the viewer
        may not be asking for — and it cannot get their filters wrong.
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
        source: str | None = None,
        outcome: str | None = None,
        skip_code: str | None = None,
        q: str | None = None,
        before_id: int | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """The board's data: recent events, deliveries, breaker and silence state as JSON."""
        _read_guard(x_read_token, authorization)
        now = now_ts()
        return {
            "queue": await app.state.store.queue_counts(),
            "fuse": app.state.fuse.snapshot(),
            "breakers": app.state.breaker.snapshot(now),
            "silences": await app.state.store.list_silences(now),
            "recent": await app.state.store.recent_events(
                limit, source=source, outcome=outcome, skip_code=skip_code, query=q, before_id=before_id
            ),
        }

    @app.get("/timeline")
    async def timeline(
        x_read_token: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        limit: int = 50,
    ) -> dict[str, Any]:
        """What happened — one stream, chains gathered, with what each one spent.

        `/status` answers "recently" and `/trace/{id}` answers "this one".
        Neither answers "what happened", and the gap was measurable: reviewing a
        deployment meant five endpoints across two machines joined by eye.

        This is a PROJECTION, not a second ledger. The pipe already records every
        hop, because every handover goes through it by construction — the only
        thing missing was a way to read that as one thing. Nothing new is asked
        of any node, which is the point: a node here may be written by somebody
        else and run somewhere else, and a store it had to write to would take
        the replaceable node down with it.

        Cost appears per hop when a return door extracts `meta.cost_usd` into a
        field, and `unpriced_hops` counts the ones where it does not — because a
        free hop and an unpriced one are different facts and only the config
        knows which is which."""
        _read_guard(x_read_token, authorization)
        rows = await app.state.store.recent_events(min(limit * 4, 400))
        return render_timeline(rows, limit=limit)

    @app.get("/trace/{event_id}")
    async def round_trip(
        event_id: int,
        x_read_token: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """One alert's whole journey: the original, where it fanned out to, and
        what each processing system sent back.

        The comparison view. Every brain received the SAME payload, so the
        differences in what came back are differences in their judgement — not
        in their input. Works from either end: ask about a return and you get
        the group assembled around its origin.
        """
        _read_guard(x_read_token, authorization)
        trip = await app.state.store.round_trip(event_id)
        if trip is None:
            raise HTTPException(status_code=404, detail="no such event")
        return trip

    @app.get("/metrics", response_class=PlainTextResponse)
    async def prometheus_metrics(
        x_read_token: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
    ) -> str:
        """Prometheus text: events by door and outcome, deliveries, outbox, breaker state."""
        # Same guard as /status: the numbers describe the estate's alert flow.
        _read_guard(x_read_token, authorization)
        now = now_ts()
        return metrics.render(
            queue=await app.state.store.queue_counts(),
            fuse=app.state.fuse.snapshot(),
            silences=len(await app.state.store.list_silences(now)),
            retention_days=app.state.settings.retention_days,
            breakers=app.state.breaker.snapshot(now),
        )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        """Liveness only — no dependencies consulted, so an unhealthy ledger cannot hide a live process."""
        return {"status": "ok"}

    # ── admin: silences ───────────────────────────────────────────────────

    def _admin_guard(token: str | None) -> None:
        # No admin token configured = the endpoints do not exist, effectively:
        # an unconfigured instance cannot be muted by whoever finds the port.
        if not token_ok(app.state.settings.admin_token, token):
            raise HTTPException(status_code=403, detail="admin token required")

    @app.post("/silences")
    async def create_silence(request: Request, x_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
        """Admin: quiet one source for a window; every silenced event still lands in the ledger."""
        _admin_guard(x_admin_token)
        data = await request.json()
        source = str(data.get("source", "*"))
        if source != "*" and source not in app.state.config.sources:
            raise HTTPException(status_code=400, detail="unknown source")
        minutes = int(data.get("minutes", 60))
        if minutes < 1 or minutes > 7 * 24 * 60:
            raise HTTPException(status_code=400, detail="minutes out of range (1..10080)")
        now = now_ts()
        silence_id = await app.state.store.add_silence(source, now + minutes * 60, str(data.get("note", "")), now)
        return {"id": silence_id, "source": source, "until_ts": now + minutes * 60}

    # ── admin: config (the FILE stays the source of truth) ───────────────
    # The page is an editor for config.yaml, not a second config store:
    # GET returns the raw text (${ENV} refs, never resolved secrets), PUT
    # validates + writes atomically + hot-swaps, reload re-reads the file.
    # Validation failure changes NOTHING — the running config keeps serving.

    def _config_summary(loaded: Config) -> dict[str, Any]:
        return {
            "sources": sorted(loaded.sources),
            "channels": sorted(loaded.channels),
            "routes": [route.name for route in loaded.routes],
            "pipeline": [stage.name for stage in loaded.pipeline],
        }

    @app.get("/config")
    async def get_config(x_admin_token: str | None = Header(default=None)) -> JSONResponse:
        """Admin: the running config file, with inline `secret:` values redacted.

        This docstring used to say "with secrets redacted" while returning the
        file's bytes unchanged. It was true only by convention — every config in
        this family writes `secret: ${NAME}` — and a convention is not what a
        promise like that should rest on, least of all in a repository where the
        docstring IS the decision record.

        What is redacted: a `secret:` whose value is written out rather than
        referenced. What is NOT, deliberately:

        - `secret: ""` stays visible. An unsigned door is a fact an admin reading
          this needs, and hiding it behind the same mask as a real credential
          would make the two indistinguishable.
        - `secret: ${NAME}` stays visible. The name is the useful part and the
          value is not here.
        - URLs. A webhook URL can BE a credential (a Lark bot URL carries its
          token in the path), and this cannot tell one from an internal service
          address — so inline such a URL and this endpoint will serve it. Write
          it as `${NAME}`; `/topology` is the view that prints host only.
        """
        _admin_guard(x_admin_token)
        path = Path(app.state.settings.config_path)
        if not path.is_file():
            raise HTTPException(status_code=404, detail=f"no config file at {path}")
        return JSONResponse({"path": str(path), "yaml": _redact_secrets(path.read_text(encoding="utf-8"))})

    @app.put("/config")
    async def put_config(request: Request, x_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
        """Admin: replace the config after a dry parse — a config that cannot load never becomes the config."""
        _admin_guard(x_admin_token)
        text = (await request.body()).decode("utf-8")
        try:
            import yaml as _yaml

            candidate = Config.from_dict(_yaml.safe_load(text) or {})
        except (ConfigError, Exception) as error:  # noqa: BLE001 — every parse error is a 400
            raise HTTPException(status_code=400, detail=f"{error.__class__.__name__}: {error}") from None
        path = Path(app.state.settings.config_path)
        # Atomic replace: never leave a half-written config on disk.
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent) or ".", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_name, path)
        app.state.config = candidate
        return {"applied": True, **_config_summary(candidate)}

    @app.post("/config/reload")
    async def reload_config(x_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
        """Admin: re-read the config file from disk, same dry-parse rule as PUT."""
        _admin_guard(x_admin_token)
        try:
            candidate = Config.from_file(app.state.settings.config_path)
        except (ConfigError, FileNotFoundError, Exception) as error:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"{error.__class__.__name__}: {error}") from None
        app.state.config = candidate
        return {"applied": True, **_config_summary(candidate)}

    # ── admin: dead-letter retry ──────────────────────────────────────────

    @app.post("/deliveries/{delivery_id}/retry")
    async def retry_delivery(delivery_id: int, x_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
        """Admin: put one dead delivery back in the queue with a fresh attempt budget."""
        _admin_guard(x_admin_token)
        if not await app.state.store.retry_delivery(delivery_id, now_ts()):
            raise HTTPException(status_code=404, detail="no dead delivery with that id")
        return {"requeued": delivery_id}

    @app.delete("/silences/{silence_id}")
    async def remove_silence(silence_id: int, x_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
        """Admin: lift a silence before its window ends."""
        _admin_guard(x_admin_token)
        if not await app.state.store.delete_silence(silence_id):
            raise HTTPException(status_code=404, detail="no such silence")
        return {"deleted": silence_id}

    return app
