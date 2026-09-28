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

from hookprobe import actions, decline, remediation
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
[{{"action": "what this does", "command": "the exact command", \
"target": "the one host, service or resource it changes", \
"risk": "low|medium|high", "rollback": "how to undo it"}}]
```
Propose only commands you are confident in; an operator approves each proposal before \
anything runs, and nothing you write here executes by itself. Name `target` the way a \
runbook would and the same way every time: it is what holds a second procedure off a \
machine this one has just changed.

Source: {source}
Level: {level}
Title: {title}
Body: {body}
Fields:
```json
{fields}
```"""


def _verdict_instruction(vocabulary: frozenset[str]) -> str:
    """Ask for a conclusion only when the deployment declared what may be said.

    Appended rather than templated in, so the two prompts stay one question each
    and neither grows a branch. Empty vocabulary (the default) appends nothing,
    which keeps every existing deployment's prompt byte-identical — a prompt
    change is a golden-replay event here, and this must not be one for anybody
    who has not opted in.

    The list is spelled out because that is the whole guarantee: the agent is
    choosing from a set an operator wrote down, and `reports.verdict` drops
    anything else. Naming the options here just saves a wasted run.
    """
    if not vocabulary:
        return ""
    options = " | ".join(sorted(vocabulary))
    return (
        "\n\nFinally, end the report with a line `VERDICT: <one of: "
        f"{options}>` — exactly one of those words, nothing else on the line. "
        "It routes what happens next. Omit the line if none of them is honestly true."
    )


_TASK_MESSAGE = """Run one read-only investigation of the work item below and answer one \
question: concretely, how would this be done?

This is NOT an alert. Nothing is broken and there is no root cause to find — somebody asked for \
something or assigned it, and the operator wants to arrive already knowing the shape of the work. \
Do not write an incident report.

Establish what is true NOW from the systems you can actually reach, then say what the work \
involves: what already exists, what is missing, the steps in order, and how the result would be \
verified. Where you cannot see something — a repository, a file, a console you have no route to — \
say so plainly under `unknowns` instead of guessing. A named gap is worth more to the operator \
than a confident guess, because the gap is what they will go look at first.

TRY BEFORE YOU DECLARE A GAP. "I have no access" is a measurement, not an assumption: run the \
command and report what it actually said. And a gap is only worth writing down if the person \
reading it can act on it, so ALSO append a fenced block naming what would open each one:
```blocked
[{{"kind": "credential", "name": "EXACT_ENV_VAR", "answers": "what this would let you conclude"}},
 {{"kind": "egress", "name": "the.host.you.could.not.reach", "answers": "..."}},
 {{"kind": "question", "name": "the one thing only the requester can answer", "answers": "..."}},
 {{"kind": "probe", "command": "one read-only command, pasteable as written", "answers": "..."}}]
```
Use `credential` when a named environment variable would answer it, `egress` when a host was \
refused, `question` when no amount of access would help because only the requester knows, and \
`probe` when someone with access could run one read-only command — write that command so it can \
be pasted verbatim, and it must read nothing you would not read yourself. At most six, most \
blocking first, and omit the block entirely when nothing blocked you.

When a gap is what stands between you and the answer, SAY THAT IN THE OPENING SENTENCE — the card \
quotes it, and "I need X to finish this" is what the operator can act on, where a plan they cannot \
check is not. Replying in this thread with the output continues this same investigation.

Open the case files first: Grep/Read /data/results/ for earlier work on the same subject.

Answer with a short Markdown report, conclusion first: the opening paragraph is a one-sentence \
answer a notification card can quote verbatim.
If this taught you a durable fact about the ENVIRONMENT itself — topology, a naming convention, \
where something lives; never about this one request — end with a line \
`MEMORY-SUGGESTION: <the fact, one line>`. At most one; omit it when unsure.

Deliberately no remediation block: this door answers how the work would be done, and the person \
who asked for it is the one who does it. Propose nothing for execution.

Source: {source}
Level: {level}
Title: {title}
Body: {body}
Fields:
```json
{fields}
```"""

# The third question, and the one that is not a question: the body IS the task.
#
# Both prompts above presume a SUBJECT to analyse — "what broke" or "how would
# this be done" — and wrap the body in that framing. A scheduled brief has
# neither shape: it is a procedure with its own steps and its own output
# contract, and the framing fights it. Measured on the first watcher round: the
# brief said "run `date`, then list conversations via MCP, then post one signal
# per finding", the alert wrapper said "find the root cause, open the case files
# first", and the run did `date`, grepped case files, called no MCP tool at all
# and answered with the brief's silence token. A blend of two instructions is
# what a wrapper around a procedure produces.
#
# NOT a trust boundary, and worth saying so where somebody might mistake it for
# one: the wrapper never sanitised anything. A caller holding the door's secret
# could always write "ignore the above" into a body. This removes framing, not a
# control, and the door's HMAC is still the whole of what decides who may speak.
_BRIEF_MESSAGE = """{body}

---
Context for the round above, not instructions:
Source: {source} · Level: {level} · Title: {title}
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
_WORK_ID_MAX = 120
_EVENT_ID_MAX = 200
_BODY_MAX = 4000
# A brief gets four times the room, and the difference is defensible for exactly
# one reason: every other body arrives from an upstream payload nobody in this
# family controls, while a brief is a file the operator wrote and signed into
# the door. Truncating an alert loses detail about one incident; truncating a
# procedure silently deletes STEPS from it, and the run then does most of a job
# and reports success. The first watcher brief landed at 3,799 bytes against the
# 4,000 cap — two more paragraphs from a failure nothing would have reported.
# Still bounded, and _EVENT_MAX_BYTES (128 KB) still gates the whole request.
_BRIEF_BODY_MAX = 16000
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


_FOLLOW_UP_MESSAGE = """Someone replied in this investigation's chat thread and asked:

{text}

Answer the question directly, in a few lines, from what this investigation has \
already gathered; run tools only if the answer needs something not yet looked at. \
Read-only, as before. Lead with the answer."""
_FOLLOW_UP_TEXT_MAX = 4000
_MAX_FOLLOW_UPS_PER_RUN = 20


def _is_recovery(event: dict[str, Any], fields: dict[str, Any]) -> bool:
    """Whether the caller SAID this event is a condition ending.

    Top level first, because that is where the pipe puts it: `is_recovery` is a
    fact about the event, not one of its extracted fields, and the pipe only
    sets it when a source template stated it. `fields` as a fallback for a door
    configured the other way round.

    Never inferred from the text. A keyword guess at "resolved" in a title is a
    judgement about content, which belongs to the brain that owns it — and the
    judge already makes it, which is why what arrives here is a stated fact.
    """
    for value in (event.get("is_recovery"), fields.get("is_recovery")):
        if isinstance(value, bool) and value:
            return True
        if isinstance(value, str) and value.strip().lower() in ("true", "1", "yes", "resolved", "ok"):
            return True
    return False


def _recovered(service: RunService, event: dict[str, Any]) -> dict[str, Any]:
    """Record that the condition ended, and spend nothing doing it."""
    title = str(event.get("title") or "").strip()[:_TITLE_MAX]
    source = str(event.get("source") or "unknown")[:_SOURCE_MAX]
    run = service.record_recovery(source, title, event_id=event.get("event_id")) if title else None
    if run is None:
        return {"status": "skipped", "reason": "recovery with no investigation of this condition to verify"}
    return {"status": "verified", "by": "recovery", "sessionKey": run.session_key}


def _work_id(fields: dict[str, Any], request: Request, session_key: str) -> str:
    """Which piece of work this run belongs to (hookprobe/work.py).

    Three sources, in this order, and none of them reads content:

      1. `fields.work_id` — a node upstream said so. This is how a plan on one
         service stitches to the work it was handed off to on another: the
         handoff carries the id, the pipe copies the field, this door adopts it.
      2. `X-Hook-Correlation-Id` — the pipe's own handle for this hop's chain,
         already on every delivery it makes. Nothing new had to be configured
         for a report and its alert to share an id.
      3. the session key, for runs nobody routed here (the console's own).
    """
    stated = str(fields.get("work_id") or "").strip()[:_WORK_ID_MAX]
    if stated:
        return stated
    return str(request.headers.get("x-hook-correlation-id") or "").strip()[:_WORK_ID_MAX] or session_key


def _sender_allowed(settings: Settings, sender: str) -> bool:
    """Who may spend a turn from chat: HOOKPROBE_FOLLOW_UP_SENDERS, `*` for anyone
    the bridge forwards, empty for nobody."""
    allowed = settings.follow_up_senders
    return bool(allowed) and bool(sender) and ("*" in allowed or sender in allowed)


def _declined(service: RunService, run: Run, reason: str, *, message_id: str = "") -> dict[str, Any]:
    """Refuse a reply, and say so where the reply was typed.

    Not every refusal in this door gets one, and the line is whether the person
    could act on knowing. These three can: a conversation at its ceiling, a
    window with no budget left, a session that can no longer be resumed. The
    other three cannot and stay quiet — an unknown thread and a redelivery are
    both the expected steady state, and telling an unlisted sender that they are
    unlisted answers a question they should have to ask a person.

    The notice costs no turn. It is a report-shaped message returned through the
    family loop, which is the one dialect a channel renderer can dress, and it
    carries the thread root so it lands as a reply rather than a new card.
    """
    service.report_unanswered(run, reason, message_id=message_id)
    return {"status": "declined", "reason": reason, "sessionKey": run.session_key}


def _follow_up(
    service: RunService, settings: Settings, event: dict[str, Any], fields: dict[str, Any]
) -> dict[str, Any]:
    """A person's reply under one of this investigation's cards, forwarded by the
    pipe with the session it resolved (`fields.session`) and the thread to answer
    in (`fields.thread_root`). Every refusal is a 200 with a reason: the pipe
    records it, and a retry would only repeat the decision.

    Four gates, in order of cost: the sender must be allowed to spend (a reply
    in a group is a paid turn anyone in the group could start); the session
    must exist here; the message must not have been answered already (the
    platform redelivers); and the budget breaker applies as it does to an
    alert. The turn itself is `continue_run`: the same engine session, so the
    answer comes from what the investigation already found, under the same
    read-only posture, and returns through the same door as the report — with
    `thread_root` in its meta so the pipe posts it as a reply, not a new card.
    """
    sender = str(fields.get("sender") or "").strip()[:_ACTOR_MAX]
    if not _sender_allowed(settings, sender):
        return {"status": "skipped", "reason": "sender not allowed to continue investigations from chat"}
    session_key = str(fields.get("session") or "").strip()[: _EVENT_ID_MAX + 80]
    run = service.get(session_key) if session_key else None
    if run is None:
        return {"status": "skipped", "reason": "no investigation behind this thread"}
    message_id = str(fields.get("message_id") or "").strip()[:_EVENT_ID_MAX]
    raw_handled = run.meta.get("follow_ups")
    handled: list[str] = [str(x) for x in raw_handled] if isinstance(raw_handled, list) else []
    if message_id and message_id in handled:
        return {"status": "already_done", "sessionKey": run.session_key}
    if len(handled) >= _MAX_FOLLOW_UPS_PER_RUN:
        return _declined(
            service,
            run,
            f"this investigation has answered its {_MAX_FOLLOW_UPS_PER_RUN} follow-ups",
            message_id=message_id,
        )
    text = str(event.get("body") or event.get("title") or "").strip()[:_FOLLOW_UP_TEXT_MAX]
    if not text:
        return {"status": "skipped", "reason": "empty question"}
    state = service.budget_state()
    if state is not None and state[0] >= state[1]:
        return _declined(service, run, "the investigation budget for this window is spent", message_id=message_id)
    try:
        run = service.continue_run(run.session_key, {"message": _FOLLOW_UP_MESSAGE.format(text=text)})
    except RunBusyError:
        return {
            "status": "busy",
            "reason": "a turn is already running; ask again when it answers",
            "sessionKey": run.session_key,
        }
    except NotResumableError:
        return _declined(service, run, "this investigation left no session to continue", message_id=message_id)
    run.meta["follow_ups"] = [*handled, message_id] if message_id else handled
    run.meta["follow_up_by"] = sender
    root = str(fields.get("thread_root") or "").strip()[:120]
    if root:
        run.meta["thread_root"] = root
    return {"status": "accepted", "sessionKey": run.session_key, "runId": run.run_id}


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

    Three handles, tried in order, none of them content. A correlation id that
    happens to BE a session key is honoured first: one lookup, and it leaves
    the pipe a way to be explicit. Then the pipe's own `hr-<id>`, which the
    door above keeps as `meta.correlation_id` and which the report echoes — so
    a button cut from the REPORT card (whose own event id is not the alert's)
    still finds the run; before that echo existed, every follow-up pressed on
    an investigation card answered "no run", because the press carried the
    report's id and nothing here had ever seen it. Last, the alert's event id
    the envelope names, which is what a button on the verdict card carries.

    Newest first, because an event id is only unique per source and the
    freshest match is the one the card carried.
    """
    if correlation_id:
        direct = service.get(correlation_id)
        if direct is not None:
            return direct
    quoted = correlation_id[3:] if correlation_id.startswith("hr-") and correlation_id[3:].isdigit() else ""
    # Compared as text: an id the pipe carried as 123 and a channel handed back
    # as "123" are the same alert, and the session key was built from the string
    # either way.
    wanted = str(event_id)[:_EVENT_ID_MAX] if event_id is not None and event_id != "" else ""
    if not quoted and not wanted:
        return None
    for run in service.list_runs(limit=500):
        meta = run.meta or {}
        if correlation_id and str(meta.get("correlation_id") or "") == correlation_id:
            return run
        if quoted and str(meta.get("event_id")) == quoted:
            return run
    for run in service.list_runs(limit=500):
        if wanted and str((run.meta or {}).get("event_id")) == wanted:
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
        row = service.approve_remediation(ref, note=note, actor=actor)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="no such proposal") from exc
    except remediation.Cooling as exc:
        # The target cooldown: another procedure acted on this same target
        # recently, and two changes to one machine inside the window with nobody
        # looking in between is a flap. Answered exactly like `Moved` below,
        # because it is the same hole — the bridge repainted the card "accepted
        # and passed on" and stripped its buttons, so a refusal nobody carries
        # back is a refusal nobody sees. The status is neither `stale` nor
        # `superseded`: this proposal is still good and still approvable, which
        # is what the report it sends back says.
        service.report_cooling(ref, str(exc))
        return {"status": "cooling", "kind": "approve", "ref": ref, "detail": str(exc)}
    except remediation.Moved as exc:
        # The freshness cursor: the condition moved between the card being sent
        # and this press. Caught before the ValueError it subclasses, because
        # this is the one refusal the operator cannot see for themselves — the
        # bridge repainted their card "accepted and passed on" the moment the
        # PIPE took the press, and it stripped the buttons on the way. So the
        # answer goes back as a report through the family loop, and the status
        # is not `stale`: nothing here is unfixable, the fix is a fresh look.
        service.report_superseded(ref, str(exc))
        return {"status": "superseded", "kind": "approve", "ref": ref, "detail": str(exc)}
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
    ruled, reconsidered = service.record_ruling(run.session_key, kind, actor=actor)
    out = {"status": "recorded", "kind": kind, "sessionKey": ruled.session_key, "ruling": ruled.ruling}
    # What the press ACCOMPLISHED, not just that it was recorded — a withdrawal is
    # the visible payoff that makes a "useless" worth pressing.
    consequence = _consequence(reconsidered)
    if consequence:
        out["consequence"] = consequence
    return out


def _consequence(reconsidered: list[dict[str, Any]]) -> str:
    """One human line for what a useless ruling did to the library, or ''."""
    withdrawn = [o["runbook"] for o in reconsidered if o["action"] == "withdrawn"]
    flagged = [o["runbook"] for o in reconsidered if o["action"] == "flagged"]
    parts = []
    if withdrawn:
        parts.append(f"withdrew {'runbook' if len(withdrawn) == 1 else 'runbooks'} {', '.join(withdrawn)}")
    if flagged:
        parts.append(f"flagged {', '.join(flagged)} for review")
    return "; ".join(parts)


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
    if kind == "remember":
        return _remember(service, params, actor=actor)
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


def _remember(service: RunService, params: dict[str, Any], *, actor: str) -> dict[str, Any]:
    """Accept one queued memory line, from a card, with one tap.

    Everything that reaches this button was REFUSED by the shape check — a line
    that could act on a later run (hookprobe.suggestions). So this is the one
    path by which such a line enters standing instruction, and it requires a
    person; what the button removes is the login, not the person.

    202-with-a-reason for a row that is already resolved or gone, like every
    other answer on this door: a card outlives the queue it was minted from, and
    a non-2xx here becomes a redelivery loop over something permanent.
    """
    ref = str(params.get("ref") or "").strip()[:_REF_MAX]
    if not ref:
        raise HTTPException(status_code=400, detail="remember needs params.ref naming the suggestion")
    row = service.accept_suggestion(ref)
    if row is None:
        return {"status": "already_resolved_or_gone", "kind": "remember", "ref": ref}
    logger.info("memory accepted from a card id=%s actor=%s", ref, actor or "-")
    return {"status": "remembered", "kind": "remember", "ref": ref, "line": str(row.get("line") or "")[:200]}


def register(app: FastAPI, settings: Settings, service: RunService) -> None:
    """Mount the two family doors. No token guard: both are signature-authenticated."""

    # Idempotent per (source, event_id) — a storm of the SAME event id funds one
    # investigation, not N (a restatement with a new id is a new investigation —
    # the budget breaker is the backstop).
    @app.post("/hooks/event")
    async def event_door(request: Request) -> dict[str, Any]:
        """The pipe's door: a signed alert either starts a paid investigation,
        coalesces into one already running, or is declined by level, budget or
        a standing ruling — every decline says why."""
        event = await _signed_object(request, settings.event_secret, _EVENT_MAX_BYTES)

        raw_fields = event.get("fields")
        fields = raw_fields if isinstance(raw_fields, dict) else {}
        if str(fields.get("kind") or "").strip().lower() == "follow_up":
            return _follow_up(service, settings, event, fields)
        # A condition that ENDED is not an investigation request. The judge
        # refuses to analyse one on its own ("the question how bad is it is moot
        # once it is over"), and this door used to treat it as a RE-FIRE of the
        # same condition — a paid turn telling the model the alert had fired
        # again when it had in fact cleared, or, outside the coalescing window,
        # a whole new investigation of something already over. It is a fact
        # about work already done, so it is recorded on that work for nothing.
        #
        # Before the level check on purpose: a recovery inherits its firing's
        # importance, which is often below the escalation bar (18 of them on the
        # production deployment, every one below it), and the fact is worth
        # recording whatever that number says.
        if _is_recovery(event, fields):
            return _recovered(service, event)

        # A run a PERSON started from chat (the pipe forwards their message with
        # `fields.sender`) passes the same gate as a follow-up: anyone in the
        # group can type, and a run is a paid turn.
        chat_sender = str(fields.get("sender") or "").strip()
        if chat_sender and not _sender_allowed(settings, chat_sender):
            return {"status": "skipped", "reason": "sender not allowed to start investigations from chat"}

        level = str(event.get("level") or "").lower()[:_LEVEL_MAX]
        title = str(event.get("title") or "").strip()[:_TITLE_MAX]
        if level not in settings.escalate_levels:
            return {"status": "skipped", "reason": f"level {level or 'unknown'} below escalation bar"}
        if not title:
            raise HTTPException(status_code=400, detail="event has no title")
        # The families this node has no instrument for are declined here, not
        # investigated and then reported unreachable. Rule-driven escalations
        # only: a person asking from chat has said they want a run.
        if not chat_sender:
            declined_by = decline.decline_reason(settings.decline_patterns, title)
            if declined_by:
                logger.info(
                    "declined at the door: no instrument for this family title=%r pattern=%r", title[:80], declined_by
                )
                decline.record(settings.workdir, title, declined_by)
                return {
                    "status": "skipped",
                    "reason": "declined: this node has no instrument for this family",
                    "pattern": declined_by,
                }

        source = str(event.get("source") or "unknown")[:_SOURCE_MAX]
        event_id = event.get("event_id")
        # The key names the session for the rest of the run's life — it is the
        # case file's name and the audit log's — so what goes in it is bounded
        # too. An id longer than this is not an identifier.
        key_id = str(event_id)[:_EVENT_ID_MAX] if event_id is not None else title[:80]
        session_key = f"probe:{source}:{key_id}"
        # An alert and a work item are different questions, and the caller says
        # which through `fields.kind` — the pipe stays content-blind, so the
        # source that raised it is the one that knows. Anything unrecognised is
        # an alert, which is what this door has always assumed.
        raw_fields = event.get("fields")
        kind = str(raw_fields.get("kind") or "").strip().lower() if isinstance(raw_fields, dict) else ""
        template = {"task": _TASK_MESSAGE, "brief": _BRIEF_MESSAGE}.get(kind, _EVENT_MESSAGE)
        message = template.format(
            source=source,
            level=level,
            title=title,
            body=str(event.get("body") or "")[: _BRIEF_BODY_MAX if kind == "brief" else _BODY_MAX],
            fields=_fenced_fields(event.get("fields")),
        ) + _verdict_instruction(settings.verdicts)
        meta: dict[str, Any] = {
            "title": title,
            "level": level,
            "source": source,
            "event_id": event_id,
            # What KIND of thing this is, as the caller named it — an alert, a
            # work item, a question. The door already branched on it to pick a
            # prompt; recording it is what lets a board group by it later.
            "kind": kind or "alert",
            "work_id": _work_id(fields, request, session_key),
        }
        # The pipe's own handle for this hop's chain, kept apart from the work
        # id (which a handoff may have stated) and echoed on the report
        # (notify.py), so the buttons the pipe cuts from that report carry the
        # ALERT's correlation and not the report's own — a press on the
        # investigation card then lands in the alert's chain, and a verdict
        # ruling forwarded to the judge names a judgement the judge has.
        correlation = str(request.headers.get("x-hook-correlation-id") or "").strip()[:_WORK_ID_MAX]
        if correlation:
            meta["correlation_id"] = correlation
        # The sending platform's own id for this alert, when the pipe carried
        # one. Echoed on the report (notify.py) so the platform's alert page can
        # find the investigation the pipe started on its own.
        reference = str(event.get("reference") or "").strip()[:200]
        if reference:
            meta["reference"] = reference
        # A run opened from a chat topic answers INTO that topic: the pipe hands
        # the topic's root along, and the report carries it back (notify.py).
        topic_root = str(fields.get("thread_root") or "").strip()[:120]
        if topic_root:
            meta["thread_root"] = topic_root
        # Who asked, when a person did: the console shows it, and a run that a
        # person opened is a different thing to read than one an alert opened.
        if chat_sender:
            meta["asked_by"] = chat_sender
        payload: dict[str, Any] = {"message": message, "sessionKey": session_key, "_meta": meta}

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
                # question the running session is not about to answer. It is
                # still evidence about any procedure this work already ran.
                service.record_refire(prior, event_id=event_id)
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
                    # A procedure that ran and the condition fired again: the
                    # procedure did not hold, and the row says so before the
                    # follow-up turn says anything.
                    service.record_refire(run, event_id=event_id)
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
        """A card press forwarded by the pipe: accept a memory line, rule a run
        useful or useless — signed, idempotent, and answered with what changed."""
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
