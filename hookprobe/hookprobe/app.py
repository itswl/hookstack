"""HTTP surface: an OpenClaw-compatible contract, plus the family's own doors.

POST /hooks/agent               -> {"runId": ...}                  (trigger)
POST /hooks/event               -> the pipe's escalation door      (family)
GET  /sessions/{key}/final      -> 200 isFinal:true / 202 / 404    (poll)
POST /sessions/{key}/continue   -> follow-up turn, same session    (explore)
GET  /v1/runs                   -> session list, newest first      (UI)
GET  /v1/runs/{key}             -> full run record with turns      (UI/debug)
GET  /ui                        -> the sessions page, unauthenticated markup
GET  /healthz                   -> liveness, unauthenticated

isFinal is always true on a 200: a run is either still going (202) or done.
That single guarantee lets a poller trust the first confirming read instead
of running stability heuristics against a moving answer.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import re
import shutil
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from hookprobe import __version__
from hookprobe.engine import _load_mcp_servers
from hookprobe.retention import prune
from hookprobe.runs import Run
from hookprobe.service import NotResumableError, NoTurnRunningError, RunBusyError, RunService
from hookprobe.settings import Settings
from hookprobe.wire import verify_timestamped

_EVENT_MESSAGE = """针对下面这条告警做一次只读深度调查：定位根因、评估影响、给出按优先级排序的处置建议。\
开工前先翻案卷：用 Grep/Read 查 /data/results/ 下的历史调查记录，若发现同类告警的过往结论，\
在报告中引用并对比（上次的判定是什么、这次是否吻合）。\
最终输出一份简明的中文 Markdown 报告，结论先行；报告第一段是一句话结论，供通知卡片直接引用。

告警来源: {source}
级别: {level}
标题: {title}
正文: {body}
附加字段:
```json
{fields}
```"""

_MEMORY_MAX_BYTES = 256 * 1024

_UI_PAGE = Path(__file__).with_name("ui.html")

# Directory names only — no separators, no leading dot, so a crafted skill
# name can never walk out of the skills directory.
_SKILL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,100}$")


def _skill_description(text: str) -> str:
    """Best-effort description from SKILL.md frontmatter."""
    if not text.startswith("---"):
        return ""
    for line in text.splitlines()[1:40]:
        if line.strip() == "---":
            break
        if line.startswith("description:"):
            return line.split(":", 1)[1].strip().strip("\"'")[:200]
    return ""


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
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        # A restart must not orphan the loop: runs a previous process left
        # mid-flight settle as failures that report themselves.
        service.sweep_orphans()

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

    # The family's event door: hookrelay's to-probe channel (generic,
    # payload: normalized) delivers judged-worthy alerts here. The pipe stays
    # content-blind, so the escalation judgement lives on this side: only
    # levels in escalate_levels start a paid investigation, everything else
    # is acknowledged and skipped. Idempotent per (source, event_id) — a storm
    # of restatements funds one investigation, not N.
    @app.post("/hooks/event")
    async def event_door(request: Request) -> dict[str, Any]:
        raw = await request.body()
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

        level = str(event.get("level") or "").lower()
        title = str(event.get("title") or "").strip()
        if level not in settings.escalate_levels:
            return {"status": "skipped", "reason": f"level {level or 'unknown'} below escalation bar"}
        if not title:
            raise HTTPException(status_code=400, detail="event has no title")

        source = str(event.get("source") or "unknown")
        event_id = event.get("event_id")
        session_key = f"probe:{source}:{event_id if event_id is not None else title[:80]}"
        message = _EVENT_MESSAGE.format(
            source=source,
            level=level,
            title=title,
            body=str(event.get("body") or "")[:4000],
            fields=json.dumps(event.get("fields") or {}, ensure_ascii=False, indent=1),
        )
        payload = {
            "message": message,
            "sessionKey": session_key,
            "_meta": {"title": title, "level": level, "source": source, "event_id": event_id},
        }

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

    # Environment memory: {workdir}/CLAUDE.md is loaded into every engine
    # session (setting_sources includes "project"), so facts written here —
    # topology, known false alarms, naming conventions — reach every
    # investigation from its first turn.
    @app.get("/v1/memory", dependencies=[Depends(require_token)])
    async def memory_read() -> dict[str, Any]:
        path = settings.workdir / "CLAUDE.md"
        content = ""
        if path.is_file():
            try:
                content = path.read_text(encoding="utf-8")
            except OSError as exc:
                raise HTTPException(status_code=500, detail=f"memory unreadable: {exc}") from exc
        return {"content": content, "path": str(path)}

    @app.put("/v1/memory", dependencies=[Depends(require_token)])
    async def memory_write(payload: dict[str, Any]) -> dict[str, Any]:
        content = payload.get("content")
        if not isinstance(content, str):
            raise HTTPException(status_code=400, detail="content must be a string")
        raw = content.encode("utf-8")
        if len(raw) > _MEMORY_MAX_BYTES:
            raise HTTPException(status_code=413, detail=f"memory exceeds {_MEMORY_MAX_BYTES} bytes")
        path = settings.workdir / "CLAUDE.md"
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(raw)
        tmp.replace(path)
        return {"saved": True, "bytes": len(raw)}

    def _skill_layers() -> list[tuple[str, Path]]:
        """The layers the ENGINE actually loads, in its precedence order —
        the browser must not show skills that no run would see."""
        layers = [("project", settings.workdir / ".claude" / "skills")]
        if "user" in settings.setting_sources:
            layers.append(("user", Path.home() / ".claude" / "skills"))
        return layers

    @app.get("/v1/skills", dependencies=[Depends(require_token)])
    async def skills_list() -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for layer, skills_dir in _skill_layers():
            if not skills_dir.is_dir():
                continue
            for entry in sorted(skills_dir.iterdir()):
                manifest = entry / "SKILL.md"
                if not (entry.is_dir() and manifest.is_file()) or entry.name in seen:
                    continue
                try:
                    text = manifest.read_text(encoding="utf-8")
                    stat = manifest.stat()
                    files = sorted(f.name for f in entry.iterdir() if f.is_file())[:20]
                except OSError:
                    continue
                seen.add(entry.name)
                out.append(
                    {
                        "name": entry.name,
                        "description": _skill_description(text),
                        "size": stat.st_size,
                        "modified": stat.st_mtime,
                        "files": files,
                        "layer": layer,
                    }
                )
        return out

    @app.get("/v1/skills/{name}", dependencies=[Depends(require_token)])
    async def skill_detail(name: str) -> dict[str, Any]:
        if not _SKILL_NAME.match(name):
            raise HTTPException(status_code=404, detail="skill not found")
        for layer, skills_dir in _skill_layers():
            manifest = skills_dir / name / "SKILL.md"
            if manifest.is_file():
                return {"name": name, "content": manifest.read_text(encoding="utf-8"), "layer": layer}
        raise HTTPException(status_code=404, detail="skill not found")

    @app.put("/v1/skills/{name}", dependencies=[Depends(require_token)])
    async def skill_write(name: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Writes always land in the project layer: editing a read-only
        user-layer skill saves a project copy that shadows it from then on."""
        if not _SKILL_NAME.match(name):
            raise HTTPException(status_code=400, detail="invalid skill name")
        content = payload.get("content")
        if not isinstance(content, str):
            raise HTTPException(status_code=400, detail="content must be a string")
        raw = content.encode("utf-8")
        if len(raw) > _MEMORY_MAX_BYTES:
            raise HTTPException(status_code=413, detail=f"skill exceeds {_MEMORY_MAX_BYTES} bytes")
        skill_dir = settings.workdir / ".claude" / "skills" / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        manifest = skill_dir / "SKILL.md"
        tmp = manifest.with_suffix(".tmp")
        tmp.write_bytes(raw)
        tmp.replace(manifest)
        return {"saved": True, "name": name, "layer": "project", "bytes": len(raw)}

    @app.delete("/v1/skills/{name}", dependencies=[Depends(require_token)])
    async def skill_delete(name: str) -> dict[str, Any]:
        """Only the project layer is deletable. Removing a shadow lets the
        user-layer skill of the same name resurface — the host copy was
        never touched."""
        if not _SKILL_NAME.match(name):
            raise HTTPException(status_code=404, detail="skill not found")
        skill_dir = settings.workdir / ".claude" / "skills" / name
        if not (skill_dir / "SKILL.md").is_file():
            for layer, layer_dir in _skill_layers():
                if layer != "project" and (layer_dir / name / "SKILL.md").is_file():
                    raise HTTPException(
                        status_code=403, detail="user-layer skills are read-only (mounted from the host)"
                    )
            raise HTTPException(status_code=404, detail="skill not found")
        shutil.rmtree(skill_dir)
        return {"deleted": True, "name": name}

    @app.get("/v1/mcp", dependencies=[Depends(require_token)])
    async def mcp_servers() -> dict[str, Any]:
        """What the next run would load — read fresh from the config file.

        Env VALUES are secrets (API tokens live there) and are never
        returned; the key names alone prove the wiring."""
        servers = _load_mcp_servers(settings.mcp_config)
        described: dict[str, Any] = {}
        for name, spec in servers.items():
            if not isinstance(spec, dict):
                described[name] = {"invalid": True}
                continue
            described[name] = {
                key: spec.get(key) for key in ("command", "args", "type", "url") if spec.get(key) is not None
            }
            described[name]["env_keys"] = sorted((spec.get("env") or {}).keys())
        return {
            "config": str(settings.mcp_config) if settings.mcp_config else None,
            "servers": described,
        }

    @app.get("/v1/budget", dependencies=[Depends(require_token)])
    async def budget() -> dict[str, Any]:
        state = service.budget_state()
        if state is None:
            return {"enabled": False}
        spent, limit = state
        return {
            "enabled": True,
            "budget_usd": limit,
            "window_hours": settings.budget_window_hours,
            "spent_usd": round(spent, 6),
            "remaining_usd": round(max(0.0, limit - spent), 6),
            "exhausted": spent >= limit,
        }

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
        running, queued = service.turn_counts()
        return {
            "status": "ok",
            "version": __version__,
            "active_runs": service.active_count(),
            # Slot arithmetic: `running` holds a semaphore slot; `queued` is
            # started but waiting for one. A storm shows up here first.
            "running_turns": running,
            "queued_turns": queued,
            # Reports that never reached the pipe — alert on this going up.
            "return_failures": service.return_failure_count(),
        }

    return app
