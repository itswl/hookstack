"""HTTP surface: the OpenClaw-compatible contract WebhookWise already speaks.

POST /hooks/agent               -> {"runId": ...}                  (trigger)
GET  /sessions/{key}/final      -> 200 isFinal:true / 202 / 404    (poll)
POST /sessions/{key}/continue   -> follow-up turn, same session    (explore)
GET  /v1/runs                   -> session list, newest first      (UI)
GET  /v1/runs/{key}             -> full run record with turns      (UI/debug)
GET  /ui                        -> the sessions page, unauthenticated markup
GET  /healthz                   -> liveness, unauthenticated

isFinal is always true on a 200: a run is either still going (202) or done.
That single guarantee is what lets WebhookWise write the result on the first
confirming poll instead of running its stability heuristics.
"""

from __future__ import annotations

import hmac
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from hookprobe import __version__
from hookprobe.runs import Run
from hookprobe.service import NotResumableError, RunBusyError, RunService
from hookprobe.settings import Settings

_UI_PAGE = Path(__file__).with_name("ui.html")


def _summary(run: Run) -> dict[str, Any]:
    title = (run.turns[0]["message"] if run.turns else run.current_message) or ""
    turn_costs = [t.get("cost_usd") for t in run.turns if t.get("cost_usd")]
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
    }


def create_app(settings: Settings, service: RunService) -> FastAPI:
    app = FastAPI(title="hookprobe", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)

    def require_token(authorization: str | None = Header(default=None)) -> None:
        if not settings.token:
            return  # explicitly unauthenticated deployment (private network only)
        expected = f"Bearer {settings.token}"
        if not (authorization and hmac.compare_digest(authorization, expected)):
            raise HTTPException(status_code=401, detail="invalid or missing bearer token")

    @app.post("/hooks/agent", dependencies=[Depends(require_token)])
    async def trigger(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            run = service.start(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"runId": run.run_id, "sessionKey": run.session_key}

    @app.get("/sessions/{session_key}/final", dependencies=[Depends(require_token)])
    async def final(session_key: str) -> JSONResponse:
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

    @app.get("/v1/runs", dependencies=[Depends(require_token)])
    async def run_list(limit: int = 100) -> list[dict[str, Any]]:
        return [_summary(run) for run in service.list_runs(limit=limit)]

    @app.get("/v1/runs/{session_key}", dependencies=[Depends(require_token)])
    async def run_detail(session_key: str) -> dict[str, Any]:
        run = service.get(session_key)
        if run is None:
            raise HTTPException(status_code=404, detail="session not found")
        return asdict(run)

    # The page itself carries no data — every call it makes presents the
    # bearer token, so serving the markup unauthenticated is safe.
    @app.get("/", include_in_schema=False)
    async def index() -> RedirectResponse:
        return RedirectResponse("/ui")

    @app.get("/ui", include_in_schema=False)
    async def ui() -> HTMLResponse:
        return HTMLResponse(_UI_PAGE.read_text(encoding="utf-8"))

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {"status": "ok", "version": __version__, "active_runs": service.active_count()}

    return app
