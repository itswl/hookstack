"""HTTP surface: an OpenClaw-compatible contract, plus the family's own doors.

POST /hooks/agent               -> {"runId": ...}                  (trigger)
POST /hooks/event               -> the pipe's escalation door      (family)
POST /hooks/action              -> a button pressed on the card    (family)
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
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse

from hookprobe import __version__, events, library, ops, remediation
from hookprobe.engine import file_fact
from hookprobe.files import system_prompt_path
from hookprobe.live import Live
from hookprobe.retention import prune
from hookprobe.runs import RUNNING, Run
from hookprobe.seeds import seed_default_agents
from hookprobe.service import NotResumableError, NoTurnRunningError, RunBusyError, RunService
from hookprobe.settings import Settings
from hookprobe.wire import constant_time_eq

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


def _summary(run: Run) -> dict[str, Any]:
    # The alert's name when the run knows it — stated by the event door or read
    # back out of a platform prompt — and only then the raw message, which for
    # agent-door runs is a page of instruction boilerplate that made the board
    # unreadable: thirty rows, one string.
    title = str(run.meta.get("title") or "") or (run.turns[0]["message"] if run.turns else run.current_message) or ""
    # `is not None`, not truthiness: a turn that genuinely cost 0.0 (a budget
    # refusal) is a counted turn, and dropping it fell back to run.cost_usd —
    # erasing the very distinction the ledger keeps between "nobody counted
    # this" (None) and "this was free" (0.0).
    turn_costs = [cost for cost in (t.get("cost_usd") for t in run.turns) if cost is not None]
    return {
        "session_key": run.session_key,
        "status": run.status,
        "created_at": run.created_at,
        "finished_at": run.finished_at,
        "turn_count": len(run.turns) + (0 if run.finished else 1),
        # The session's whole bill, not the last turn's.
        "cost_usd": sum(turn_costs) if turn_costs else run.cost_usd,
        "model": run.model,
        "engine_session_id": run.engine_session_id,
        "title": title[:120],
        "origin": run.origin,
        "return_status": run.return_status,
        # What the run left for the next one: {"installed": name} or
        # {"skipped": reason}, empty when the loop is off.
        "distilled": dict(run.distilled),
        # "useful" / "useless" / "" — a person's ruling on whether this
        # investigation earned its bill. On the summary rather than only the
        # detail record, because the aggregate on /v1/budget is unreadable
        # without being able to see which run it counted.
        "ruling": run.ruling,
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

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        # First boot on a fresh volume: a few readable subagent roles, so the
        # agents page teaches by example instead of starting empty.
        seed_default_agents(settings.workdir)
        # A restart must not orphan the loop: runs a previous process left
        # mid-flight settle as failures that report themselves, and an approved
        # procedure it died in the middle of stops claiming to be running.
        service.sweep_orphans()
        service.sweep_interrupted_remediations()

        async def retention_loop() -> None:
            while True:
                await asyncio.to_thread(prune, settings.workdir, Path.home(), settings.retention_days)
                await asyncio.sleep(86400)

        pruner = asyncio.create_task(retention_loop()) if settings.retention_days > 0 else None
        try:
            yield
        finally:
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

    def require_token(authorization: str | None = Header(default=None)) -> None:
        if not settings.token:
            return  # explicitly unauthenticated deployment (private network only)
        expected = f"Bearer {settings.token}"
        if not (authorization and constant_time_eq(expected, authorization)):
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
            # A run still in flight has not earned a verdict yet.
            runs = [run for run in runs if not run.ruling and run.status != RUNNING]
        return [_summary(run) for run in runs]

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
                    run = service.rule(str(key), "" if ruling == "clear" else ruling, ruled_by=by)
                except ValueError as exc:
                    results["rejected"].append({"sessionKey": key, "reason": str(exc)})
                    continue
                (results["filed"] if run is not None else results["unknown"]).append(str(key))
        investigations, useful, useless = service.window_rulings()
        results["window"] = {"investigations": investigations, "useful": useful, "useless": useless}
        return results

    @app.get("/v1/runs/{session_key}", dependencies=[Depends(require_token)])
    async def run_detail(session_key: str) -> dict[str, Any]:
        """One run whole: turns, meta, ruling, cost."""
        run = service.get(session_key)
        if run is None:
            raise HTTPException(status_code=404, detail="session not found")
        return {**asdict(run), "inputs_now": _prompt_digests_now(settings)}

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

    @app.get("/v1/remediations", dependencies=[Depends(require_token)])
    async def remediations_list() -> dict[str, Any]:
        """Open remediation proposals, newest first."""
        return {"proposals": remediation.list_all(settings.workdir)}

    @app.post("/v1/remediations/{proposal_id}/approve", dependencies=[Depends(require_token)])
    async def remediation_approve(proposal_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """The one click that makes anything run. Refused whole unless EVERY
        step passes the allowlist — a procedure that half-executes is worse
        than one that never starts."""
        try:
            row = service.approve_remediation(proposal_id, note=str((payload or {}).get("note") or ""))
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
    ops.register(app, settings, service, require_token)

    return app
