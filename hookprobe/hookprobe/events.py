"""The family's event door: the one route that spends money without being asked.

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

The two prompts live here rather than in a template file because they are the
door's contract with the model: what a first investigation is asked for, and
what a re-fire of the same condition is asked for instead. A storm of the same
condition funds one investigation and then follow-up turns inside it, which is
the cheapest correct answer — the first pass already mapped the condition, and a
follow-up keeps everything it gathered in context.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI, HTTPException, Request

from hookprobe.service import NotResumableError, RunBusyError, RunService
from hookprobe.settings import Settings
from hookprobe.wire import verify_timestamped

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


def register(app: FastAPI, settings: Settings, service: RunService) -> None:
    """Mount the event door. No token guard: this door is signature-authenticated."""

    # Idempotent per (source, event_id) — a storm of the SAME event id funds one
    # investigation, not N (a restatement with a new id is a new investigation —
    # the budget breaker is the backstop).
    @app.post("/hooks/event")
    async def event_door(request: Request) -> dict[str, Any]:
        # Refused on the declared length first, so an oversize delivery is not
        # read into memory before being turned away; the second check catches a
        # sender that declared nothing.
        declared = request.headers.get("Content-Length") or ""
        if declared.isdigit() and int(declared) > _EVENT_MAX_BYTES:
            raise HTTPException(status_code=413, detail=f"event exceeds {_EVENT_MAX_BYTES} bytes")
        raw = await request.body()
        if len(raw) > _EVENT_MAX_BYTES:
            raise HTTPException(status_code=413, detail=f"event exceeds {_EVENT_MAX_BYTES} bytes")
        if not verify_timestamped(
            settings.event_secret,
            raw,
            request.headers.get("X-Hook-Signature"),
            request.headers.get("X-Hook-Timestamp"),
        ):
            raise HTTPException(status_code=401, detail="bad signature")
        try:
            event = json.loads(raw)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="body is not JSON") from exc
        if not isinstance(event, dict):
            raise HTTPException(status_code=400, detail="body is not an object")

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
