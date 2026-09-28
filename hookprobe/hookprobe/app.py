"""HTTP surface: an OpenClaw-compatible contract, plus hookstack's own doors.

POST /hooks/agent               -> {"runId": ...}                  (trigger)
POST /hooks/event               -> the pipe's escalation door      (hookstack)
POST /hooks/action              -> a button pressed on the card    (hookstack)
GET  /sessions/{key}/final      -> 200 isFinal:true / 202 / 404    (poll)
POST /sessions/{key}/continue   -> follow-up turn, same session    (explore)
GET  /v1/runs                   -> session list, newest first      (UI)
GET  /v1/runs/{key}             -> full run record with turns      (UI/debug)
GET  /ui                        -> the sessions page, unauthenticated markup
GET  /healthz                   -> liveness, unauthenticated

isFinal is always true on a 200: a run is either still going (202) or done.
That single guarantee lets a poller trust the first confirming read instead
of running stability heuristics against a moving answer.

What stays in this module is the contract above, the two live streams, and the
routes that act on a run: start it, follow it, stop it, approve the procedure it
proposed. The rest of the surface is grouped by what it is about and mounted from
there — hookprobe.events owns the family doors and their prompts, hookprobe.library
the files a person edits and a run reads, hookprobe.ops the read-only view of
what this process is doing. Each of them takes the settings and the service
explicitly and registers its own routes; the bearer-token dependency is defined
once here and handed over, because an auth rule duplicated per module is an auth
rule that will eventually differ per module.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.error
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse

from hookprobe import (
    __version__,
    audit,
    automation,
    describe,
    events,
    handoff,
    library,
    ops,
    posture,
    remediation,
    rulings,
    selftest,
    suggestions,
    telemetry,
    work,
)
from hookprobe.distill import slug
from hookprobe.engine import file_fact
from hookprobe.files import system_prompt_path
from hookprobe.live import Live
from hookprobe.retention import prune
from hookprobe.runs import INFERRED_BY_PREFIX, RUNNING, Run, turn_cost
from hookprobe.service import NotResumableError, NoTurnRunningError, RunBusyError, RunService
from hookprobe.settings import Settings
from hookprobe.wire import constant_time_eq

logger = logging.getLogger("hookprobe.app")

_UI_PAGE = Path(__file__).with_name("ui.html")


def _ndjson(payload: dict[str, Any]) -> bytes:
    """One JSON object, one line — the whole wire format."""
    return (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")


def _prompt_files(settings: Settings) -> dict[str, Path]:
    """The two editable prompt inputs, keyed by the name a run records them under."""
    return {
        "memory": settings.workdir / "CLAUDE.md",
        "system_prompt_append": system_prompt_path(settings),
    }


def _run_links(board: str, meta: dict[str, Any]) -> dict[str, str]:
    """Where this run's chain lives in the pipe — when the deployment has said
    where its board is (HOOKPROBE_RELAY_UI_URL). The board resolves `#chain=`
    by the hop's event id, so the id the pipe handed this run is enough."""
    event_id = meta.get("event_id")
    if not board or event_id is None:
        return {}
    return {"chain": f"{board.rstrip('/')}/#chain={event_id}"}


def _prompt_digests_now(settings: Settings) -> dict[str, str | None]:
    """Those same files as they stand right now, for comparing against a record.

    A run records the digest of the memory and methodology it was handed. That
    digest is identical on every run until somebody edits the file, so on its own
    it reads as a meaningless constant. What an operator wants when opening an
    old report is the comparison — does the file still say what it said then? —
    and only the read path can answer it. None means the file is absent now,
    which is itself a difference worth showing.
    """
    digests: dict[str, str | None] = {}
    for name, path in _prompt_files(settings).items():
        fact = file_fact(path)
        digests[name] = fact["sha256"] if fact else None
    return digests


def _summary(run: Run, price: Callable[[Any], float | None] | None = None) -> dict[str, Any]:
    # The alert's name when the run knows it — stated by the event door or read
    # back out of a platform prompt — and only then the raw message, which for
    # agent-door runs is a page of instruction boilerplate that made the board
    # unreadable: thirty rows, one string.
    title = str(run.meta.get("title") or "") or (run.turns[0]["message"] if run.turns else run.current_message) or ""
    # `is not None`, not truthiness: a turn that genuinely cost 0.0 (a budget
    # refusal) is a counted turn, and dropping it fell back to run.cost_usd —
    # erasing the very distinction the ledger keeps between "nobody counted
    # this" (None) and "this was free" (0.0).
    # Priced the way the budget window prices them (runs.turn_cost), so the
    # spend bars and this row add up to what the breaker counts. The recorded
    # figure rides alongside when repricing changed it — the run is not
    # rewritten, and a reader comparing against an old export can see why.
    turn_costs = [cost for cost in (turn_cost(t, price) for t in run.turns) if cost is not None]
    recorded = [cost for cost in (t.get("cost_usd") for t in run.turns) if cost is not None]
    return {
        "session_key": run.session_key,
        "status": run.status,
        "created_at": run.created_at,
        "finished_at": run.finished_at,
        "turn_count": len(run.turns) + (0 if run.finished else 1),
        # The session's whole bill, not the last turn's.
        "cost_usd": sum(turn_costs) if turn_costs else run.cost_usd,
        **(
            {"recorded_cost_usd": round(sum(recorded), 6)}
            if price is not None and recorded and abs(sum(recorded) - sum(turn_costs)) > 1e-6
            else {}
        ),
        "model": run.model,
        "model_endpoint": run.model_endpoint,
        "engine_session_id": run.engine_session_id,
        "title": title[:120],
        "origin": run.origin,
        # A re-fire answered from a runbook cost $0 and ran no engine; the weekly
        # cost report counts these as what the runbook loop avoided.
        "answered_from_runbook": bool(run.meta.get("answered_from_runbook")),
        # A drill or a by-hand check: real machinery on unreal work. The weekly
        # page and the board leave it out, and it anchors nothing.
        "synthetic": bool(run.meta.get("synthetic")),
        "return_status": run.return_status,
        # What the run left for the next one: {"installed": name} or
        # {"skipped": reason}, empty when the loop is off.
        "distilled": dict(run.distilled),
        # "useful" / "useless" / "" — the ruling on whether this investigation
        # earned its bill. On the summary rather than only the detail record,
        # because the aggregate on /v1/budget is unreadable without being able
        # to see which run it counted.
        "ruling": run.ruling,
        # And WHO said so, which is the half that decides what the number means.
        # A patrol can infer a ruling from a run's own evidence (ruled_by carries
        # the "patrol:" prefix for exactly that); a person clicking a list cannot
        # be told apart from it once the prefix is dropped. Every ruling on this
        # deployment today is patrol-inferred — 13 useless against 6 useful — and
        # a board that showed only `ruling` was answering "was this worth paying
        # for?" with the model's own opinion of itself.
        "ruled_by": run.ruled_by,
        "ruled_at": run.ruled_at,
        "inferred": run.ruled_by.startswith(INFERRED_BY_PREFIX),
        # How often the posture refused this session, summed over its turns.
        # On the SUMMARY and not only the detail, for the reason the ruling is:
        # a number recorded where nobody looks is a number nobody acts on, and
        # this one was added the same afternoon its own note complained about
        # exactly that.
        "guard_trips": sum(int(t.get("guard_trips") or 0) for t in run.turns),
    }


def _is_operator_request(request: Request, payload: dict[str, Any]) -> bool:
    """Whether a PERSON asked for this investigation rather than a rule.

    Two ways to say so, because the header survives a proxy that rewrites bodies
    and the body field survives a client that cannot set headers. Absence means
    automated — the conservative direction, since a refused person can retry
    with the header and an overspent budget cannot be un-spent.
    """
    header = str(request.headers.get("x-operator") or "").strip().lower()
    if header in {"1", "true", "yes"}:
        return True
    return bool(payload.get("operator") is True)


def create_app(settings: Settings, service: RunService) -> FastAPI:
    # The board's change signal. The service already knows when a run's state
    # moves; this is where that becomes something a browser can wait on.
    live = Live()
    service.on_board_change = live.changed
    # Created here rather than inside the lifespan so `/v1/ops` can read the last
    # verdict: an operator asking "are the boundaries still holding" gets the
    # answer the watch already computed, not another seven demonstrations.
    watch = selftest.Watch(settings, service.alarm) if settings.selftest_every_seconds > 0 else None

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        # First boot on a fresh volume: a few readable subagent roles, so the
        # agents page teaches by example instead of starting empty.
        # A restart must not orphan the loop: runs a previous process left
        # mid-flight settle as failures that report themselves, and an approved
        # procedure it died in the middle of stops claiming to be running.
        # Before the first run: are the credentials as narrow as the posture
        # says? Under enforce a wider-than-declared runner does not come up.
        await posture.on_startup(
            settings.workdir, settings.bash_guard, settings.posture_check, radius=settings.blast_radius
        )
        service.recover_orphans()
        service.sweep_interrupted_remediations()

        async def retention_loop() -> None:
            while True:
                await asyncio.to_thread(prune, settings.workdir, Path.home(), settings.retention_days)
                await asyncio.sleep(86400)

        pruner = asyncio.create_task(retention_loop()) if settings.retention_days > 0 else None
        # And the boundaries, on a clock. `/v1/selftest` shipped run by nothing,
        # which makes it a claim rather than a check; this is what turns it into
        # one. A failure goes to the alarm channel — the same door the pipe's own
        # delivery failure uses, because "a boundary did not hold" is that same
        # kind of news and must not travel through the thing that broke.
        watcher = asyncio.create_task(watch.loop(settings.selftest_every_seconds)) if watch is not None else None
        try:
            yield
        finally:
            if watcher is not None:
                watcher.cancel()
            if pruner is not None:
                pruner.cancel()
            # The other side of the sweep above: give the work in flight a
            # moment to record itself, so a graceful stop leaves less for the
            # next boot to clean up than a crash does.
            await service.shutdown()

    app = FastAPI(
        title="hookprobe",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    def require_token(request: Request, authorization: str | None = Header(default=None)) -> None:
        """Two bearers, and the difference between them is the method.

        `settings.token` is the console's and opens everything. `agent_token` is
        the one the AGENT holds, and it is refused on every method but GET —
        because the run-rulings patrol needs to READ `/v1/runs`, and nothing the
        agent does needs to write through this API at all: it proposes its
        rulings as lines in its report and the service files them.

        The rule is the method rather than a route list on purpose. A list is a
        thing to forget to add to: every write route added after this would have
        defaulted to reachable, which is the wrong direction for a door whose
        whole point is what it refuses. GET is a claim this codebase already
        keeps elsewhere — no handler here mutates on a read.
        """
        if not settings.token:
            return  # explicitly unauthenticated deployment (private network only)
        if authorization and constant_time_eq(f"Bearer {settings.token}", authorization):
            return
        if (
            request.method == "GET"
            and settings.agent_token
            and authorization
            and constant_time_eq(f"Bearer {settings.agent_token}", authorization)
        ):
            return
        raise HTTPException(status_code=401, detail="invalid or missing bearer token")

    @app.post("/hooks/agent", dependencies=[Depends(require_token)])
    async def trigger(payload: dict[str, Any], request: Request) -> dict[str, Any]:
        """Start an investigation from a finished prompt (idempotent per sessionKey).

        A condition under a standing not_worth_it ruling is answered from its
        runbook at no cost; `{"force": true}` insists on a real engine run.

        The budget breaker guards spending nobody asked for, and until recently it
        inferred that from the DOOR: this one was "operator-driven" so it was
        never gated. That stopped being true when a platform upstream began
        forwarding matching alerts here automatically, so the door now carries
        both a person's question and a rule's decision — and refusing the whole
        door would refuse the person, which is the failure the original design
        existed to avoid.

        So HOOKPROBE_BUDGET_GATES_AGENT_DOOR arms the meter — and a caller that
        is a PERSON can say so (`X-Operator` header, or `operator` in the body)
        and be answered anyway. The default direction is deliberate: silence is
        treated as automated, so an unmarked caller is refused rather than
        spending freely. Arming the meter and then discovering a forgotten header
        had uncapped it is the worse failure, and a person who is refused can
        retry with the header, which a budget cannot do in reverse.
        """
        if settings.budget_gates_agent_door and not _is_operator_request(request, payload):
            state = service.budget_state()
            if state is not None:
                spent, limit = state
                # Existing sessions stay reachable: a redelivery or a poll of an
                # already-funded run must never bounce off the meter.
                if spent >= limit and service.get(str(payload.get("sessionKey") or "")) is None:
                    run = service.refuse_for_budget(payload, origin="", spent=spent)
                    return {"runId": run.run_id, "sessionKey": run.session_key, "status": "refused"}
        try:
            run = service.start(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"runId": run.run_id, "sessionKey": run.session_key}

    @app.get("/sessions/{session_key}/final", dependencies=[Depends(require_token)])
    async def final(session_key: str) -> JSONResponse:
        """Poll for the finished report: 202 while running, then the full text once."""
        run = service.get(session_key)
        if run is None:
            raise HTTPException(status_code=404, detail="session not found")
        if not run.finished:
            return JSONResponse(status_code=202, content={"isProcessing": True})
        return JSONResponse(content={"isFinal": True, "text": run.text, "messageCount": run.message_count})

    @app.post("/sessions/{session_key}/continue", dependencies=[Depends(require_token)])
    async def continue_session(session_key: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Follow-up turn in a finished investigation; poll /final for the answer."""
        try:
            run = service.continue_run(session_key, payload)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RunBusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (NotResumableError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"runId": run.run_id, "sessionKey": run.session_key}

    @app.get("/v1/live", dependencies=[Depends(require_token)])
    async def board_events() -> StreamingResponse:
        """The session list's wake-up line, the same shape the other two boards use.

        The per-run stream below carries a run's steps; this one carries only
        "something moved" — a run started, finished, or returned its report —
        so the list refetches itself without the page keeping a clock.
        """
        return StreamingResponse(
            live.stream(),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.get("/v1/runs", dependencies=[Depends(require_token)])
    async def run_list(limit: int = 100, unruled: bool = False) -> list[dict[str, Any]]:
        """Finished runs, newest first. `unruled=1` narrows to the ones awaiting a verdict.

        The filter exists so "what do I still owe an opinion on" is one request
        rather than a scan: the cost side of every investigation is measured to
        the cent and the worth side is measured only where somebody rules.
        """
        runs = service.list_runs(limit=limit)
        if unruled:
            # A run still in flight has not earned a verdict yet, and a notice
            # never will: it is this service explaining itself, and "was it
            # worth it" is a question about investigations.
            runs = [
                run for run in runs if not run.ruling and run.status != RUNNING and not (run.meta or {}).get("notice")
            ]
        price = service.pricer()
        return [_summary(run, price) for run in runs]

    @app.post("/v1/runs/{session_key}/ruling", dependencies=[Depends(require_token)])
    async def rule_one_run(session_key: str, payload: dict[str, Any]) -> dict[str, Any]:
        """One run, ruled from the sessions page — and told what the ruling DID.

        The card buttons were the only way to rule, and on a deployment with no
        chat bridge there are no cards, so the loops that feed on a ruling could
        not be fed at all. This is the same ruling from the surface an operator
        actually reviews on: the page, reachable on the laptop with no inbound
        path. `record_ruling` runs in-process here, so unlike the card path it
        can report the CONSEQUENCE synchronously — "withdrew the runbook this run
        taught" — which is the payoff that makes pressing worth the click.

        `{ruling: "useful"|"useless"|"clear"}`.
        """
        want = str(payload.get("ruling") or "")
        by = str(payload.get("by") or "operator")
        try:
            _run, reconsidered = service.record_ruling(session_key, "" if want == "clear" else want, actor=by)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"ruling": want, "consequence": events._consequence(reconsidered)}

    @app.post("/v1/runs/rulings", dependencies=[Depends(require_token)])
    async def file_run_rulings(payload: dict[str, Any]) -> dict[str, Any]:
        """File verdicts on several investigations at once — was this RUN worth it.

            {"useless": ["<sessionKey>", ...], "useful": [...], "by": "<optional id>"}

        Nested under /v1/runs on purpose, because this service now has two kinds
        of ruling pointing opposite ways: `hookprobe.rulings` is what the
        investigator concludes about a CONDITION and files with the judge, and it
        has teeth — a standing not_worth_it answers repeats from the runbook. A
        run ruling is what a PERSON concludes about one investigation, and it
        spends nothing and gates nothing; it only feeds the worth column of the
        budget report. Sharing the bare word would have invited a future change
        to give one the other's consequences.

        Bulk because the friction was never the opinion, it was having to express
        it eighteen times. This service's own notes record the outcome of the
        per-item path — "nobody presses the buttons on the cards" — so the door
        that finally exists takes a list.
        """
        by = str(payload.get("by") or "")
        results: dict[str, Any] = {"filed": [], "unknown": [], "rejected": []}
        for ruling in ("useful", "useless", "clear"):
            keys = payload.get(ruling) or []
            if not isinstance(keys, list):
                results["rejected"].append({"ruling": ruling, "reason": "expected a list of session keys"})
                continue
            for key in keys:
                try:
                    service.record_ruling(str(key), "" if ruling == "clear" else ruling, actor=by)
                except ValueError as exc:
                    results["rejected"].append({"sessionKey": key, "reason": str(exc)})
                    continue
                except LookupError:
                    results["unknown"].append(str(key))
                    continue
                results["filed"].append(str(key))
        investigations, useful, useless, inferred = service.window_rulings()
        results["window"] = {
            "investigations": investigations,
            "useful": useful,
            "useless": useless,
            "inferred": inferred,
        }
        return results

    @app.post("/v1/rulings", dependencies=[Depends(require_token)])
    async def file_condition_ruling(payload: dict[str, Any]) -> dict[str, Any]:
        """A person rules on a CONDITION — is this alert worth investigating again.

            {"title": "<alert title>", "verdict": "worth_it" | "not_worth_it", "why": "...", "by": "<optional id>"}

        The condition axis had one writer, the weekly ai-rulings patrol, and no
        door for a person: `rulings.jsonl` rows carried no author and `standing`
        was latest-wins, so a verdict written by hand would have been overwritten
        by the next Thursday's inference — measured on production 2026-09-14,
        where the two SES conditions that took 70% of the week's investigator
        spend were ruled worth_it by the patrol and nobody could say otherwise.
        This files the ruling with `ruled_by: operator:<by>`, which `standing`
        prefers over an inferred one while it is current. Same TTL as any
        ruling, because a decision nobody re-checks is still a prejudice with a
        timestamp, and the reverify clause still buys a real run on schedule.

        Console bearer only — the agent's is refused on every non-GET — so an
        injected instruction cannot rule its own condition not worth looking at.
        The answer says what the ruling DOES, because a verdict that gates
        nothing yet should not read as if it did.
        """
        title = str(payload.get("title") or "").strip()
        verdict = str(payload.get("verdict") or "").strip()
        why = str(payload.get("why") or "").strip()
        by = str(payload.get("by") or "").strip() or "console"
        if not title or verdict not in rulings.VERDICTS or not why:
            raise HTTPException(status_code=400, detail="needs title, verdict worth_it|not_worth_it, and why")
        row = rulings.file_operator_ruling(settings.workdir, title=title, verdict=verdict, why=why, by=by)
        current = rulings.standing(settings.workdir, title, ttl_days=settings.ruling_ttl_days)
        runbook_present = (settings.workdir / ".claude" / "skills" / slug(title) / "SKILL.md").is_file()
        if verdict == "not_worth_it" and runbook_present:
            consequence = "re-fires answer from the runbook at $0 while a real run inside the reverify window vouches"
        elif verdict == "not_worth_it":
            consequence = "no runbook to answer from yet; the ruling stands and gates nothing until one is distilled"
        else:
            consequence = "worth_it: re-fires keep buying a real investigation"
        return {
            "title": title,
            "verdict": verdict,
            "ruled_by": f"{rulings.OPERATOR_PREFIX}:{by}",
            "identity": row["identity"],
            "standing": current is not None and current.get("verdict") == verdict,
            "ttl_days": settings.ruling_ttl_days,
            "runbook_present": runbook_present,
            "consequence": consequence,
        }

    @app.get("/v1/runs/{session_key}", dependencies=[Depends(require_token)])
    async def run_detail(session_key: str) -> dict[str, Any]:
        """One run whole: turns, meta, ruling, cost."""
        run = service.get(session_key)
        if run is None:
            raise HTTPException(status_code=404, detail="session not found")
        record = asdict(run)
        price = service.pricer()
        if price is not None:
            # Each turn priced as the budget window prices it; the recorded
            # figure kept beside it when the two differ. The run file is not
            # touched — this is how it READS at this node's declared rates.
            for turn in record["turns"]:
                cost = turn_cost(turn, price)
                if cost is not None and turn.get("cost_usd") is not None and abs(cost - float(turn["cost_usd"])) > 1e-6:
                    turn["recorded_cost_usd"] = turn["cost_usd"]
                    turn["cost_usd"] = cost
            last = record["turns"][-1] if record["turns"] else None
            if last is not None and "recorded_cost_usd" in last and record.get("cost_usd") is not None:
                record["recorded_cost_usd"] = record["cost_usd"]
                record["cost_usd"] = last["cost_usd"]
        return {
            **record,
            "inputs_now": _prompt_digests_now(settings),
            "links": _run_links(settings.relay_ui_url, run.meta),
            # What this run's tool output appeared to contain. Read from the
            # audit on the DETAIL and not the list: one file scan for one run a
            # person opened, rather than one per row of a 200-row board.
            "output_secrets": audit.output_secrets(settings.workdir / "audit", session_key),
        }

    @app.get("/v1/posture", dependencies=[Depends(require_token)])
    async def posture_record() -> dict[str, Any]:
        """What the credentials could do when this runner started, measured
        against the declared posture — the record behind "this ran read-only"."""
        record = posture.read(settings.workdir)
        if record is None:
            raise HTTPException(status_code=404, detail="no posture check recorded")
        return record

    @app.get("/v1/runs/{session_key}/patch", dependencies=[Depends(require_token)])
    async def run_patch(session_key: str) -> Response:
        """The diff a work run left behind, as text/plain — reviewable, quotable,
        `git apply`-able. 404 when the run produced none: an absent patch is not
        an error to render, it is "nothing to review".

        The agent wrote the block this file comes from, so the file is a copy of
        what it CLAIMED, not ground truth — the clone's commit history is. This
        route exists so the reviewer does not have to know where the clone
        lives; the verification that the two agree is the review itself.
        """
        path = settings.workdir / "patches" / f"{session_key}.patch"
        try:
            body = path.read_text(encoding="utf-8")
        except OSError:
            raise HTTPException(status_code=404, detail="no patch on this run") from None
        return Response(content=body, media_type="text/plain; charset=utf-8")

    @app.get("/v1/runs/{session_key}/audit", dependencies=[Depends(require_token)])
    async def run_audit(session_key: str) -> dict[str, Any]:
        """The accountability record of one run: what posture it held, what it
        cost, every tool call it made and every one the guards refused, and
        whether its steering inputs changed under it. Assembled from the run
        record and the flight recorder — the two places that were written at
        the time — never from today's configuration."""
        run = service.get(session_key)
        if run is None:
            raise HTTPException(status_code=404, detail="session not found")
        lines: list[dict[str, Any]] = []
        for day_file in sorted((settings.workdir / "audit").glob("*.jsonl")):
            try:
                for raw in day_file.read_text(encoding="utf-8").splitlines():
                    if raw.strip():
                        line = json.loads(raw)
                        if line.get("session") == session_key:
                            lines.append(line)
            except (OSError, json.JSONDecodeError):
                continue
        return {
            "session_key": run.session_key,
            "run_id": run.run_id,
            "status": run.status,
            "origin": run.origin,
            "model": run.model,
            "finished_at": run.finished_at,
            "cost_usd": run.cost_usd,
            "meta": run.meta,
            "ruling": {"verdict": run.ruling, "by": run.ruled_by, "at": run.ruled_at},
            "posture": (run.inputs or {}).get("posture"),
            # The startup measurement behind that declaration, as it stood most recently.
            "startup_posture": posture.read(settings.workdir),
            "inputs": run.inputs,
            "tool_calls": [x for x in lines if not x.get("denied")],
            "denied": [x for x in lines if x.get("denied")],
            # Where the run's time went, from its own telemetry (telemetry.py).
            "telemetry": telemetry.summarize(telemetry.read(settings.workdir, session_key))["summary"],
        }

    @app.get("/v1/runs/{session_key}/telemetry", dependencies=[Depends(require_token)])
    async def run_telemetry(session_key: str) -> dict[str, Any]:
        """The shape of one run: every model call and tool call on one time axis,
        with what each cost, from the telemetry the CLI posted to this service.
        Empty for a run that reported none (receiver off, or an engine that does
        not emit) — empty, not absent, so the page can say so."""
        if service.get(session_key) is None:
            raise HTTPException(status_code=404, detail="session not found")
        shape = telemetry.summarize(telemetry.read(settings.workdir, session_key), service.pricer())
        return {"session_key": session_key, **shape}

    @app.post("/otel/v1/{signal}")
    async def otel_ingest(signal: str, request: Request) -> JSONResponse:
        """OTLP/http-json receiver for the CLI this service launches — and nothing
        else: the per-process header is the credential, the run's session-key
        attribute is the address, and a body for a run this service does not
        know is dropped and counted. Traces are accepted so an exporter that
        sends them does not log errors, and kept nowhere: the CLI emits events
        and counters, and those are what the waterfall is drawn from. When the
        service itself has a collector named, the untouched body goes on to it
        on a thread, after the response — a slow collector may not slow a run.
        """
        if signal not in telemetry.SIGNALS:
            raise HTTPException(status_code=404, detail="unknown signal")
        if not telemetry.authorized(request.headers.get(telemetry.INGEST_HEADER)):
            raise HTTPException(status_code=401, detail="not the CLI this service launched")
        raw = await request.body()
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="body is not JSON") from exc
        parsed = (
            telemetry.parse_logs(body)
            if signal == "logs"
            else telemetry.parse_metrics(body)
            if signal == "metrics"
            else {}
        )
        accepted = dropped = 0
        for key, lines in parsed.items():
            if service.get(key) is None:
                dropped += len(lines)
                continue
            accepted += telemetry.append(settings.workdir, key, lines)
        if dropped:
            logger.warning("telemetry for %d event(s) dropped: no such run here", dropped)
        collector = telemetry.collector()
        if collector:
            headers = telemetry.collector_headers()

            async def send() -> None:
                await asyncio.to_thread(telemetry.forward, signal, raw, collector, headers)

            asyncio.get_running_loop().create_task(send())
        # partialSuccess is what an OTLP exporter expects to find; the counts are ours.
        return JSONResponse({"partialSuccess": {}, "accepted": accepted, "dropped": dropped})

    @app.get("/v1/runs/{session_key}/stream", dependencies=[Depends(require_token)])
    async def run_stream(session_key: str) -> StreamingResponse:
        """The open session's steps, pushed as they happen (NDJSON, one per line).

        The console used to learn a run had progressed only on the next refresh
        tick, which defaults to a minute — so the moment right after sending a
        message, the one moment that wants immediate feedback, was the emptiest.
        This is a push instead of a faster clock: no second timer to keep in
        sync with the shared refresh control, and nothing polls when nobody is
        watching.

        NDJSON over `fetch`, not `text/event-stream` over `EventSource`: this
        page authenticates every call with a bearer token, and EventSource
        cannot set headers, which would have meant the token in a query string.
        """
        run = service.get(session_key)
        if run is None:
            raise HTTPException(status_code=404, detail="session not found")

        queue = service.watch(session_key)

        async def lines() -> AsyncIterator[bytes]:
            try:
                # Open with what already happened, so a watcher that arrives
                # mid-run is not blind to the steps it missed. Not named
                # `snapshot`: that is distill's manifest backup.
                opened = service.get(session_key)
                yield _ndjson(
                    {
                        "type": "snapshot",
                        "status": opened.status if opened else "unknown",
                        "events": list(opened.events) if opened else [],
                    }
                )
                while True:
                    current = service.get(session_key)
                    finished = current is None or current.finished
                    # Drain before deciding: a run that just settled may still
                    # have its last steps queued, and closing on them would lose
                    # exactly the part the watcher was waiting for.
                    while True:
                        try:
                            queued = queue.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                        if queued.get("type") != "settled":
                            yield _ndjson(queued)
                    if finished:
                        yield _ndjson({"type": "done", "status": current.status if current else "unknown"})
                        return
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15)
                    except TimeoutError:
                        # Idle keepalive: proxies drop silent connections, and a
                        # thinking model is silent for a long time.
                        yield _ndjson({"type": "ping"})
                        continue
                    if event.get("type") == "settled":
                        continue  # loop re-reads the run, drains, and closes
                    yield _ndjson(event)
            finally:
                service.unwatch(session_key, queue)

        return StreamingResponse(
            lines(),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.post("/sessions/{session_key}/stop", dependencies=[Depends(require_token)])
    async def stop_session(session_key: str) -> dict[str, Any]:
        """Cancel the in-flight turn; it settles as a failed turn within a poll."""
        try:
            run = service.stop(session_key)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except NoTurnRunningError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"status": "stopping", "sessionKey": run.session_key}

    @app.post("/v1/runs/{session_key}/handoff", dependencies=[Depends(require_token)])
    async def run_handoff(session_key: str) -> dict[str, Any]:
        """Hand this run's report to the pipe, for whichever node the operator
        wired that door to.

        The only human step in a chain that is otherwise automatic, and it is
        there because the next node has credentials that change things. What the
        click means is "I have read this plan" — not "I trust this pipeline".

        409 rather than 200 for a run that is still moving or produced nothing:
        an empty handoff is a paid run started on nothing at all.
        """
        run = service.get(session_key)
        if run is None:
            raise HTTPException(status_code=404, detail="session not found")
        try:
            sent = await asyncio.to_thread(handoff.send, settings.handoff_url, settings.handoff_secret, run, run.text)
        except handoff.NotConfigured as exc:
            raise HTTPException(status_code=501, detail=str(exc)) from exc
        except handoff.NotFinished as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (OSError, urllib.error.URLError) as exc:
            # Named rather than swallowed: the operator pressed a button and is
            # owed the reason it did not land, in the words the door used.
            raise HTTPException(status_code=502, detail=f"the pipe refused or was unreachable: {exc}") from exc
        return {"handed_off": True, "session": session_key, **sent}

    @app.get("/v1/automation", dependencies=[Depends(require_token)])
    async def automation_review(cls: str | None = None) -> dict[str, Any]:
        """Each class of automation, its declared ceiling, its record, and
        whether the two agree — plus the tier the record WOULD support.

        Counters, not agreement rates: every number traces to a human press or a
        sampling review. The page an operator reads before deciding whether a
        class has earned a step up, and the same view the sampling patrol reads.
        """
        return automation.review(settings.workdir, cls, tiers=settings.automation_tiers)

    @app.post("/v1/automation/{cls}/{item_id}/regret", dependencies=[Depends(require_token)])
    async def automation_regret(cls: str, item_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """A sampling review, after the fact, saying an auto-applied action was
        wrong. This is how an unattended deployment gets its human-in-the-loop
        back — asynchronously, because nobody here answers in real time — and the
        one event that resets a class's argument for a higher tier.

        Deliberately write-gated to the operator token, not reachable by any run:
        a regret is a label, and a label the automation could write about itself
        is not a label.

        This docstring used to end "the agent's subprocess holds no token", and
        that sentence was FALSE for its whole life — the agent inherited
        HOOKPROBE_TOKEN and could have posted here. It is true in substance now
        and for a different reason: the agent holds a bearer of its own, and
        `require_token` refuses it on every method but GET. Not "no token"; a
        token this door will not take.
        """
        automation.record(
            settings.workdir, cls, item_id, "regretted", note=str((payload or {}).get("note") or "")[:300]
        )
        return {"recorded": True, "class": cls, "id": item_id}

    @app.post("/v1/runs/{session_key}/retry", dependencies=[Depends(require_token)])
    async def retry_run(session_key: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Human takeover: try a failed investigation again from the board.

        The work board's `needs a human` column is where this is pressed. It
        continues the engine session when the failure left one, so the second
        attempt starts from what the first gathered rather than from nothing.
        """
        try:
            run = service.retry(session_key, by=str((payload or {}).get("by") or "operator"))
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RunBusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ValueError, NotResumableError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "status": "retrying",
            "sessionKey": run.session_key,
            "runId": run.run_id,
            "resumed": bool(run.engine_session_id),
            "attempt": int(run.meta.get("retries") or 1) + 1,
        }

    @app.get("/v1/work", dependencies=[Depends(require_token)])
    async def work_board(limit: int = 200) -> dict[str, Any]:
        """The board: one row per piece of work, not per run.

        Derived on every request from the run records, the proposal files and
        the suggestion queue — see hookprobe/work.py for why nothing here is
        stored a second time. `counts` is the header line an operator reads
        first: how much is blocked, how much is in flight, and how much closed
        without anybody having to step in.
        """
        items = work.resolve(
            service.list_runs(limit=limit),
            price=service.pricer(),
            proposals=remediation.list_all(settings.workdir, limit=200),
            suggestions=[row for row in suggestions.load(settings.workdir) if str(row.get("status") or "") == "open"],
        )
        return {"counts": work.counts(items), "items": [item.as_dict() for item in items]}

    @app.get("/v1/agent", dependencies=[Depends(require_token)])
    async def agent_card() -> dict[str, Any]:
        """What this node IS: identity, runtime, the policy it runs under, health.

        A deployment runs several of these and they were told apart only by
        port — every board said "hookprobe" and every report came from
        "hookprobe". Nothing here is new state; it is the settings this process
        actually resolved, answered in one place so a board can name the agent
        that did the work. Secrets never appear, in keeping with /v1/config.
        """
        running, queued = service.turn_counts()
        return {
            "name": settings.agent_name,
            "role": settings.agent_role,
            "version": __version__,
            # Two adapters now, and this field is the reason the selection is a
            # registry rather than an `if`: a node reporting one runtime while
            # running another is exactly the claim the contract exists to keep
            # honest. See hookprobe/tests/test_runtime_contract.py.
            "runtime": {
                "adapter": settings.runtime,
                "model": settings.model,
                "endpoint": settings.model_endpoint,
            },
            "workspace": str(settings.workdir),
            "policy": {
                "bash_guard": settings.bash_guard,
                "escalate_levels": sorted(settings.escalate_levels),
                "budget_usd": settings.budget_usd,
                "budget_window_hours": settings.budget_window_hours,
                "chat_senders": sorted(settings.follow_up_senders),
                "hands_off": bool(settings.handoff_url),
                "verdicts": sorted(settings.verdicts),
            },
            "health": {
                "active_runs": service.active_count(),
                "turns_running": running,
                "turns_queued": queued,
                "return_failures": service.return_failure_count(),
            },
        }

    @app.get("/v1/agent/description", dependencies=[Depends(require_token)])
    async def agent_description() -> dict[str, Any]:
        """The same node in ANP's dialect: what it can be asked, and what stops for a person.

        Token-guarded like everything else, deliberately. An Agent Description
        is *meant* to be crawlable, and on a deployment with a reachable
        address that is the point — but these nodes are loopback-bound, so
        opening a door to publish a document nobody outside can fetch would
        widen the surface and buy nothing. The document itself withholds the
        model, the gateway endpoint and the workspace for the same reason; see
        describe.py.
        """
        return describe.agent_description(settings, version=__version__)

    @app.get("/v1/selftest", dependencies=[Depends(require_token)])
    async def node_selftest() -> dict[str, Any]:
        """Every boundary this node claims, demonstrated right now.

        `/healthz` says the process is up. `/v1/posture` says what the
        credentials allowed at STARTUP. `/v1/agent` says what the settings
        asked for. All three are descriptions, and the failure this service
        keeps meeting is a boundary being absent while every surface still
        reads fine — a spawned gate that could not import its own package, an
        egress allowlist whose bypass was one shell prefix, a price knob no
        compose could pass.

        So this one does the things: refuses a tool no posture permits, in
        process and through the subprocess path; refuses a mutation and an
        egress bypass at the shell guard; asks its own proxy for a name nobody
        listed; presents the agent's bearer to a write route; re-measures the
        credentials rather than reading the boot record.

        Spends nothing and runs no model. A check that cannot run reports
        `held: null` and is listed under `unproven` — never a pass, for the
        reason the guard fails closed.
        """
        return await selftest.run(settings)

    @app.get("/v1/remediations", dependencies=[Depends(require_token)])
    async def remediations_list() -> dict[str, Any]:
        """Open remediation proposals, newest first, each saying whether it is
        still runnable.

        `expired` is `remediation.stale` — the SAME function `approve` refuses
        on and the work board reads — rather than a second opinion computed
        here. Two readers disagreeing about one fact is what this fixes: the
        board has always excluded a stale proposal from `blocked` (it cannot be
        cleared, so offering it is dead weight), while the approvals page
        filtered on `status` alone and counted three 47-to-55-hour-old rows as
        "procedures waiting for approval", each with a button the card no longer
        draws and a click the gate refuses.

        Reported, not rewritten. The honest alternative — rejecting them — would
        record three decisions nobody made and destroy the distinction between
        "somebody looked and said no" and "nobody came", which on an unattended
        deployment is most of what the board is for.
        """
        now = time.time()
        rows = remediation.list_all(settings.workdir)
        for row in rows:
            row["expired"] = row.get("status") == "proposed" and remediation.stale(row, now)
            # And the other refusal a reader cannot derive from the row itself:
            # the cooldown lives in the OTHER rows. Reported for the same reason
            # `expired` is — the console's button is the one surface that can
            # still be pressed after the card's is gone, and it should say why
            # the press would be refused before somebody makes it.
            row["cooling"] = (
                remediation.cooling(row, rows, window=settings.remediation_cooldown_seconds, now=now)
                if row.get("status") == "proposed"
                else ""
            )
            # And for a row that ran: what the condition said about it since.
            # Computed here, where the board and the smoke read it, from the
            # same function the work board verifies with.
            row["outcome"], row["outcome_reason"] = remediation.outcome(row, now)
        return {"proposals": rows}

    @app.post("/v1/remediations/{proposal_id}/approve", dependencies=[Depends(require_token)])
    async def remediation_approve(proposal_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """The one click that makes anything run. Refused whole unless EVERY
        step passes the allowlist — a procedure that half-executes is worse
        than one that never starts.

        `{by: ...}` names the person, the way the ruling door's does; absent, the
        row says "console", which is honest about a click behind a shared
        bearer and is at least not blank. The card door fills the same field
        with the IM user id, so a reader of the row can tell the two apart.
        """
        body = payload or {}
        try:
            row = service.approve_remediation(
                proposal_id, note=str(body.get("note") or ""), actor=str(body.get("by") or "console")[:120]
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return {"approved": True, "id": row["id"], "status": row["status"]}

    @app.post("/v1/remediations/{proposal_id}/reject", dependencies=[Depends(require_token)])
    async def remediation_reject(proposal_id: str) -> dict[str, Any]:
        """Refuse a parked proposal; it keeps its file, marked rejected."""
        try:
            row = service.reject_remediation(proposal_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"rejected": True, "id": row["id"]}

    # The page itself carries no data — every call it makes presents the
    # bearer token, so serving the markup unauthenticated is safe.
    @app.get("/", include_in_schema=False)
    async def index() -> RedirectResponse:
        """Redirects to the board."""
        return RedirectResponse("/ui")

    @app.get("/ui", include_in_schema=False)
    async def ui() -> HTMLResponse:
        """The operator board."""
        return HTMLResponse(_UI_PAGE.read_text(encoding="utf-8"))

    # The rest of the surface, grouped by what it is about. The event door takes
    # no token guard on purpose: it is authenticated by hookrelay's signature.
    events.register(app, settings, service)
    library.register(app, settings, service, require_token)
    ops.register(app, settings, service, require_token, watch=watch)

    return app
