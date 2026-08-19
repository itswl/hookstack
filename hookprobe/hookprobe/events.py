"""The family's two doors: alerts in, and the button a person pressed on the card.

hookrelay's to-probe channel delivers judged-worthy alerts here. The pipe stays
content-blind by design, so the escalation judgement lives on this side — only
levels in escalate_levels fund an investigation, everything else is acknowledged
and skipped. This is also the only mutating route with no bearer token: it is
authenticated by the signature hookrelay signs the delivery with, which is why
an empty HOOKPROBE_EVENT_SECRET is the one open door __main__ shouts about at
boot.

Everything else in here exists because the alert text is not ours. It arrives
from an upstream payload nobody in this family controls, and it becomes a
prompt: so the body caps, the fenced fields, and the bounded session key are all
the same rule applied to each field in turn. A 5 MB `fields` object once reached
the model verbatim — on the token bill of every turn of that investigation, and
in its case file forever.

The three prompts live here rather than in a template file because they are the
doors' contract with the model: what a first investigation is asked for, what a
re-fire of the same condition is asked for instead, and what a person pressing a
button on the card is asking. A storm of the same condition funds one
investigation and then follow-up turns inside it, which is the cheapest correct
answer — the first pass already mapped the condition, and a follow-up keeps
everything it gathered in context.

/hooks/action is the second door and sits here for the same reason: it is
hookrelay talking, authenticated the same way, and it resumes the sessions the
door above created. What it adds is a path back from a delivered report — the
card used to be a dead end, and reaching a follow-up or an approval meant
leaving the chat for a console URL and a bearer token. The vocabulary of what a
card may ask, and the ledger that keeps a redelivered press from spending twice,
are hookprobe.actions'.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from hookprobe import actions
from hookprobe.runs import Run
from hookprobe.service import NotResumableError, RunBusyError, RunService
from hookprobe.settings import Settings
from hookprobe.wire import verify_timestamped

logger = logging.getLogger("hookprobe.events")

_EVENT_MESSAGE = """Run one read-only investigation of the alert below: find the root cause, \
assess the impact, and give remediation steps in priority order.
Open the case files first: Grep/Read /data/results/ for earlier investigations of the same \
alert, and if you find one, cite it and compare — what was the previous verdict, does this \
one agree.
Answer with a short Markdown report, conclusion first: the opening paragraph is a \
one-sentence conclusion a notification card can quote verbatim.
If this investigation taught you a durable fact about the ENVIRONMENT itself — topology, \
a known false alarm, a naming convention; never about this one incident — end the report \
with a line `MEMORY-SUGGESTION: <the fact, one line>`. At most one; omit it when unsure.
If concrete commands would remediate the root cause, ALSO append a fenced block:
```remediation
[{{"action": "what this does", "command": "the exact command", "target": "what it touches", \
"risk": "low|medium|high", "rollback": "how to undo it"}}]
```
Propose only commands you are confident in; an operator approves each proposal before \
anything runs, and nothing you write here executes by itself.

Source: {source}
Level: {level}
Title: {title}
Body: {body}
Fields:
```json
{fields}
```"""

_REFIRE_MESSAGE = """The same alert fired again — this is a follow-up in the investigation you \
already ran, not a new incident.

Level now: {level}
Title: {title}
Body: {body}
Fields:
```json
{fields}
```

Compare against your previous conclusion: has anything changed (worse, better, different \
symptom)? If your conclusion stands, restate it in one sentence and say it stands. If it \
does not, say what changed and revise the remediation order. Keep it short; the channels \
already carry your full report."""

_FOLLOWUP_MESSAGE = """A person reading your report in a chat channel pressed a button to ask this. \
Answer it directly and briefly — they are on a phone, and your report is already in front of them.

{prompt}"""

# What one alert may spend on the prompt. `body` was capped from the start; the
# rest of these are the same rule applied to the other fields, which the pipe
# fills in from an upstream payload nobody in this family controls. A 5 MB
# `fields` object reached the model verbatim — on the token bill of every turn
# of that investigation, and in its case file forever.
_EVENT_MAX_BYTES = 128 * 1024
_LEVEL_MAX = 40
_TITLE_MAX = 300
_SOURCE_MAX = 120
_EVENT_ID_MAX = 200
_BODY_MAX = 4000
_FIELDS_MAX = 4000

# What one button press may spend on the prompt and on the record. A press
# carries far less than an alert, but every one of these fields is still text
# from a channel callback, and `prompt` in particular becomes a paid turn's
# instruction — so the same rule applies field by field.
_ACTION_MAX_BYTES = 16 * 1024
_KIND_MAX = 40
_PROMPT_MAX = 2000
_REF_MAX = 64
_ACTOR_MAX = 120
_CORRELATION_MAX = 200


def _fenced_fields(fields: Any) -> str:
    """An alert's structured fields as the prompt shows them, bounded.

    Truncating JSON leaves the fence holding something that is not JSON, so the
    cut says so on its own line rather than letting the model guess where the
    object went — and says how much it is missing, which is the part that tells
    an operator the alert itself needs trimming upstream.
    """
    text = json.dumps(fields or {}, ensure_ascii=False, indent=1)
    if len(text) <= _FIELDS_MAX:
        return text
    return f"{text[:_FIELDS_MAX]}\n… truncated: {len(text)} characters of fields, {_FIELDS_MAX} shown"


async def _signed_object(request: Request, secret: str, max_bytes: int) -> dict[str, Any]:
    """This delivery's JSON body, bounded and signature-checked.

    Both doors in this module read their body exactly this way, and they share
    the code for the reason app.py gives for defining its bearer dependency once
    and handing it around: a rule about who may talk to us, restated per route,
    is a rule that will eventually differ per route. Both of these are hookrelay
    talking, and the signature is the whole of what says so.
    """
    # Refused on the declared length first, so an oversize delivery is not read
    # into memory before being turned away; the second check catches a sender
    # that declared nothing.
    declared = request.headers.get("Content-Length") or ""
    if declared.isdigit() and int(declared) > max_bytes:
        raise HTTPException(status_code=413, detail=f"body exceeds {max_bytes} bytes")
    raw = await request.body()
    if len(raw) > max_bytes:
        raise HTTPException(status_code=413, detail=f"body exceeds {max_bytes} bytes")
    if not verify_timestamped(
        secret,
        raw,
        request.headers.get("X-Hook-Signature"),
        request.headers.get("X-Hook-Timestamp"),
    ):
        raise HTTPException(status_code=401, detail="bad signature")
    try:
        body = json.loads(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="body is not JSON") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body is not an object")
    return body


def _resolve_run(service: RunService, correlation_id: str, event_id: Any) -> Run | None:
    """Which investigation a card button belongs to.

    hookprobe never sees the pipe's correlation id. What both sides do agree on
    is the alert's event id: the door below names a session `probe:{source}:{id}`
    from the delivery it was handed, and the report that returns echoes the
    source and the id back in `meta` — which is what the card was cut from. So
    the id comes home on the press and this walks the recent runs for the
    session whose meta holds it.

    Newest first, because an event id is only unique per source and the freshest
    match is the one the card carried. A correlation id that happens to BE a
    session key is honoured before any of that: it costs one lookup, and it
    leaves the pipe a way to be explicit if it ever wants one.
    """
    if correlation_id:
        direct = service.get(correlation_id)
        if direct is not None:
            return direct
    if event_id is None or event_id == "":
        return None
    # Compared as text: an id the pipe carried as 123 and a channel handed back
    # as "123" are the same alert, and the session key was built from the string
    # either way.
    wanted = str(event_id)[:_EVENT_ID_MAX]
    for run in service.list_runs(limit=500):
        if str((run.meta or {}).get("event_id")) == wanted:
            return run
    return None


def _followup(service: RunService, run: Run, params: dict[str, Any]) -> dict[str, Any]:
    """Resume the investigation with the question the card carried.

    The console's follow-up path exactly — one continue, one turn, the whole
    evidence trail still in the engine session. Not budget-gated, and
    deliberately so: the breaker guards the one door that spends without a human
    asking, and a person pressing a button in a chat window is the human asking.
    """
    prompt = str(params.get("prompt") or "").strip()[:_PROMPT_MAX] or actions.followup_prompt(run)
    try:
        resumed = service.continue_run(run.session_key, {"message": _FOLLOWUP_MESSAGE.format(prompt=prompt)})
    except RunBusyError:
        return {
            "status": "busy",
            "kind": "followup",
            "sessionKey": run.session_key,
            "detail": "a turn is already in flight for this investigation",
        }
    except NotResumableError:
        return {
            "status": "not_resumable",
            "kind": "followup",
            "sessionKey": run.session_key,
            "detail": "this investigation left no engine session to resume",
        }
    return {
        "status": "investigating",
        "kind": "followup",
        "sessionKey": resumed.session_key,
        "runId": resumed.run_id,
    }


def _approve(service: RunService, params: dict[str, Any], *, actor: str, correlation_id: str) -> dict[str, Any]:
    """The operator's click, arriving from a card instead of from the console.

    A press stands in for the click and for nothing else. The allowlist is
    untouched: it answers "what class of command may ever run here", it is a file
    an operator edits on the host, and no button, no IM user and not the pipe can
    reach it — so the blast radius of a press is exactly the blast radius of a
    click, and a denial comes back as a denial rather than as a widened gate.

    What a press adds is a WHO, which the console click never had, so the actor
    and the card's correlation id go onto the row as its approving note. That is
    the line that tells a card approval from a console one afterwards.
    """
    ref = str(params.get("ref") or "").strip()[:_REF_MAX]
    if not ref:
        raise HTTPException(status_code=400, detail="approve needs params.ref naming the proposal")
    note = f"card press by {actor or 'an unnamed operator'} ({correlation_id or 'no correlation id'})"
    try:
        row = service.approve_remediation(ref, note=note)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="no such proposal") from exc
    except ValueError as exc:
        # Already approved, rejected, or settled by the boot sweep. Not an error
        # the presser can fix, and not a second execution either.
        return {"status": "stale", "kind": "approve", "ref": ref, "detail": str(exc)}
    except PermissionError as exc:
        # The allowlist gate, doing its job. A press is a click; it is not an
        # allowlist entry, and the honest answer travels back to the chat.
        return {"status": "denied", "kind": "approve", "ref": ref, "detail": str(exc)}
    return {"status": "approved", "kind": "approve", "ref": ref, "state": str(row.get("status") or "")}


def _rule(service: RunService, run: Run, kind: str, *, actor: str) -> dict[str, Any]:
    """The human's ruling on the report — the half of the bill nothing measured."""
    ruled = service.record_ruling(run.session_key, kind, actor=actor)
    return {"status": "recorded", "kind": kind, "sessionKey": ruled.session_key, "ruling": ruled.ruling}


def _dispatch(
    service: RunService,
    kind: str,
    params: dict[str, Any],
    *,
    correlation_id: str,
    event_id: Any,
    actor: str,
) -> dict[str, Any]:
    """One press, one action, once the claim on it is held.

    `approve` resolves no session on purpose: params.ref names the proposal
    directly, and the proposal carries its own session_key. The other three are
    about a conversation, so for them the session is the thing that has to exist.
    """
    if kind == "approve":
        return _approve(service, params, actor=actor, correlation_id=correlation_id)
    run = _resolve_run(service, correlation_id, event_id)
    if run is None:
        # 202-with-a-reason, not 404. A card in a chat outlives its run —
        # retention prunes case files — so somebody scrolling up and pressing a
        # stale button is the expected steady state, not a fault. The pipe reads
        # a non-2xx as a delivery failure, so a 404 here would retry with
        # backoff, dead-letter, and fire the self-alarm: the one alarm that must
        # not cry wolf, for a miss that is permanent anyway (_resolve_run has
        # already scanned the disk).
        #
        # The line: what an OPERATOR must fix stays non-2xx and earns the alarm
        # (401 the secrets disagree, 400 the shape is wrong). The world having
        # moved on is 202 — the same information, no retry storm. hookjudge's
        # /feedback answers its equivalent the same way.
        return {"status": "no_such_investigation", "kind": kind, "correlation_id": correlation_id}
    if kind == "followup":
        return _followup(service, run, params)
    return _rule(service, run, kind, actor=actor)


def register(app: FastAPI, settings: Settings, service: RunService) -> None:
    """Mount the two family doors. No token guard: both are signature-authenticated."""

    # Idempotent per (source, event_id) — a storm of the SAME event id funds one
    # investigation, not N (a restatement with a new id is a new investigation —
    # the budget breaker is the backstop).
    @app.post("/hooks/event")
    async def event_door(request: Request) -> dict[str, Any]:
        event = await _signed_object(request, settings.event_secret, _EVENT_MAX_BYTES)

        level = str(event.get("level") or "").lower()[:_LEVEL_MAX]
        title = str(event.get("title") or "").strip()[:_TITLE_MAX]
        if level not in settings.escalate_levels:
            return {"status": "skipped", "reason": f"level {level or 'unknown'} below escalation bar"}
        if not title:
            raise HTTPException(status_code=400, detail="event has no title")

        source = str(event.get("source") or "unknown")[:_SOURCE_MAX]
        event_id = event.get("event_id")
        # The key names the session for the rest of the run's life — it is the
        # case file's name and the audit log's — so what goes in it is bounded
        # too. An id longer than this is not an identifier.
        key_id = str(event_id)[:_EVENT_ID_MAX] if event_id is not None else title[:80]
        session_key = f"probe:{source}:{key_id}"
        message = _EVENT_MESSAGE.format(
            source=source,
            level=level,
            title=title,
            body=str(event.get("body") or "")[:_BODY_MAX],
            fields=_fenced_fields(event.get("fields")),
        )
        payload = {
            "message": message,
            "sessionKey": session_key,
            "_meta": {"title": title, "level": level, "source": source, "event_id": event_id},
        }

        # Storm coalescing: a re-fire of the same condition (same source+title,
        # NEW event id — redelivery of the same id stays idempotent below)
        # joins the session that already investigated it instead of funding a
        # cold start. The judge's reuse route stops verdict storms; this stops
        # investigation storms, and does it with a follow-up turn, which keeps
        # everything the first pass gathered in context.
        if service.get(session_key) is None:
            prior = service.same_alert(source, title, settings.coalesce_window_seconds)
            if prior is not None and not prior.finished:
                # Already being investigated right now; the re-fire adds no
                # question the running session is not about to answer.
                return {
                    "status": "coalesced",
                    "state": "investigating",
                    "sessionKey": prior.session_key,
                    "runId": prior.run_id,
                }
            if prior is not None:
                budget = service.budget_state()
                if budget is not None and budget[0] >= budget[1]:
                    # A follow-up spends money too. The original report has
                    # already been delivered; standing on it is not a drop.
                    return {
                        "status": "skipped",
                        "reason": "budget exhausted; the previous report stands",
                        "sessionKey": prior.session_key,
                    }
                refire = _REFIRE_MESSAGE.format(
                    level=level,
                    title=title,
                    body=str(event.get("body") or "")[:_BODY_MAX],
                    fields=_fenced_fields(event.get("fields")),
                )
                try:
                    run = service.continue_run(prior.session_key, {"message": refire})
                except RunBusyError:
                    return {
                        "status": "coalesced",
                        "state": "investigating",
                        "sessionKey": prior.session_key,
                        "runId": prior.run_id,
                    }
                except NotResumableError:
                    pass  # engine session gone; fall through to a fresh start
                else:
                    run.meta["refires"] = int(run.meta.get("refires") or 0) + 1
                    run.meta["level"] = level
                    return {"status": "coalesced", "sessionKey": run.session_key, "runId": run.run_id}

        # The budget breaker guards this door only — the one path that spends
        # money without a human asking. A refusal is not a silent drop: it
        # settles as a report-shaped run and returns through the family loop,
        # so the channels say WHY there is no investigation. Redelivery of an
        # already-funded session stays idempotent and is never refused.
        state = service.budget_state()
        if state is not None:
            spent, limit = state
            if spent >= limit and service.get(session_key) is None:
                run = service.refuse_for_budget(payload, origin="relay", spent=spent)
                return {
                    "status": "refused",
                    "reason": "budget exhausted",
                    "sessionKey": run.session_key,
                    "runId": run.run_id,
                }

        run = service.start(payload, origin="relay")
        return {"status": "accepted", "sessionKey": run.session_key, "runId": run.run_id}

    # The card's way back in, and the only other route hookrelay may open without
    # a bearer token — for the event door's reason: the signature IS the pipe's
    # credential. A person presses a button in Feishu, the pipe verifies the
    # token it minted for that card and owns everything channel-shaped about it,
    # and the press arrives here as a kind and some opaque params.
    #
    # The status codes are deliberately narrow. 202 for every delivery this door
    # processed, INCLUDING the ones it refused: an allowlist denial, a proposal
    # somebody already approved and a card whose investigation has since been
    # pruned are all answers a person needs to read in a chat window, and an HTTP
    # error on an IM callback path becomes a retry loop instead of a message.
    # Non-202 is reserved for what an OPERATOR must fix — 401 the secrets
    # disagree, 400 the shape is wrong, 404 a proposal id that never existed.
    @app.post("/hooks/action")
    async def action_door(request: Request) -> JSONResponse:
        body = await _signed_object(request, settings.event_secret, _ACTION_MAX_BYTES)
        action = body.get("action")
        if not isinstance(action, dict):
            raise HTTPException(status_code=400, detail="action must be an object")
        kind = str(action.get("kind") or "").strip().lower()[:_KIND_MAX]
        if kind not in actions.KINDS:
            raise HTTPException(status_code=400, detail=f"unknown action kind {kind or '(empty)'}")
        raw_params = action.get("params")
        params = raw_params if isinstance(raw_params, dict) else {}
        correlation_id = str(body.get("correlation_id") or "")[:_CORRELATION_MAX]
        actor = str(body.get("actor") or "")[:_ACTOR_MAX]

        # Claimed before anything is dispatched: this is the door that starts
        # paid turns and runs commands against live targets, and an IM platform
        # retries a callback it did not hear an answer to. One press, one turn.
        ledger_key = actions.key(correlation_id, kind, body.get("at"))
        try:
            seen = actions.claim(settings.workdir, ledger_key)
        except OSError as exc:
            # Fail closed. Without the claim there is nothing between a
            # redelivery and a second paid turn, and a degraded mode whose
            # degradation is "spends twice" is not one worth having.
            raise HTTPException(status_code=503, detail="cannot record the press; refusing to act twice") from exc
        if seen is not None:
            recorded = seen.get("answer")
            if isinstance(recorded, dict):
                return JSONResponse(status_code=202, content={**recorded, "duplicate": True})
            return JSONResponse(status_code=202, content={"status": "in_flight", "kind": kind, "duplicate": True})

        try:
            answer = _dispatch(
                service,
                kind,
                params,
                correlation_id=correlation_id,
                event_id=body.get("event_id"),
                actor=actor,
            )
        except Exception:
            # Nothing happened — an unknown session, an unknown proposal, or a
            # crash — so the key goes back. Holding it would answer the
            # redelivery that arrives after somebody fixes the target with
            # "already in flight" on behalf of a claim with nothing behind it.
            actions.release(settings.workdir, ledger_key)
            raise
        actions.settle(settings.workdir, ledger_key, answer)
        logger.info(
            "card action kind=%s status=%s correlation=%s actor=%s",
            kind,
            answer.get("status"),
            correlation_id or "-",
            actor or "-",
        )
        return JSONResponse(status_code=202, content=answer)
