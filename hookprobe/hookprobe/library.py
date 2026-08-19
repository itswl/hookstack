"""The editable library: the files that steer a run, and the doors a person edits them through.

Everything a run reads before it starts thinking lives on the volume — the
environment memory (CLAUDE.md), the appended methodology, the runbooks earlier
runs distilled, the subagent roles — and every one of them is hot-read, so an
edit here applies to the next run with no restart. That is the whole reason these
endpoints exist as a group: the console is the editor for the prompt.

The separation that makes it safe is not who may write but *through which door*.
The agent's own Write and Edit cannot reach any of these paths (hookprobe.inputs
explains why a run that can edit its future instructions is one injected line
away from teaching itself forward). These routes run in the service, on an
operator's request, and are untouched by that guard. Same files, different act.

Three rules hold across the whole surface:

* Writes land in the project layer, always. Editing a read-only user-layer skill
  saves a project copy that shadows it from then on; deleting the shadow lets the
  host copy resurface, because it was never touched.
* No write destroys what was there. The displaced manifest is snapshotted into
  history/ first, which is the undo the review page stands on.
* "Reviewed" only ever means somebody looked. Saving an edit flips it, and so
  does the review endpoint, which exists for the other outcome of a review — the
  text was fine as the run wrote it — that previously could not be recorded at
  all, so an approved runbook kept its unreviewed badge forever.

The memory-suggestion queue belongs here for the same reason: it is how a run
proposes a line for CLAUDE.md without being able to write one. A person accepts
or dismisses; the memory itself is never machine-written.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException

from hookprobe import suggestions
from hookprobe.distill import draft_skill, record_revision, snapshot
from hookprobe.engine import _load_agents_raw
from hookprobe.files import atomic_write, system_prompt_path
from hookprobe.service import RunService
from hookprobe.settings import Settings

# One ceiling for every file in the library. These are prompts a person
# maintains, and a megabyte of them is a mistake — one that would be paid for on
# every run that loads the file, not just once here.
_MAX_BYTES = 256 * 1024

# Directory names only — no separators, no leading dot, so a crafted skill
# name can never walk out of the skills directory.
_SKILL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,100}$")


def _require_name(name: str, *, kind: str) -> None:
    """404 on a name that could not name anything: a read of what cannot exist."""
    if not _SKILL_NAME.match(name):
        raise HTTPException(status_code=404, detail=f"{kind} not found")


def _require_writable_name(name: str, *, kind: str) -> None:
    """400 on a write to an unusable name — the caller's payload is wrong, which
    is a different answer from "no such thing"."""
    if not _SKILL_NAME.match(name):
        raise HTTPException(status_code=400, detail=f"invalid {kind} name")


def _checked_bytes(content: Any, *, kind: str) -> bytes:
    """A PUT body's text as bytes, or the 400/413 that says why not."""
    if not isinstance(content, str):
        raise HTTPException(status_code=400, detail="content must be a string")
    raw = content.encode("utf-8")
    if len(raw) > _MAX_BYTES:
        raise HTTPException(status_code=413, detail=f"{kind} exceeds {_MAX_BYTES} bytes")
    return raw


def _layers(settings: Settings, kind: str) -> list[tuple[str, Path]]:
    """The layers the ENGINE actually loads, in its precedence order —
    the browser must not show entries that no run would see."""
    layers = [("project", settings.workdir / ".claude" / kind)]
    if "user" in settings.setting_sources:
        layers.append(("user", Path.home() / ".claude" / kind))
    return layers


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


def _skill_origin(entry: Path) -> dict[str, Any]:
    """Provenance for a runbook auto-distill wrote, or nothing for a hand-made one."""
    try:
        raw = json.loads((entry / "origin.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        "written_by": str(raw.get("written_by") or ""),
        "reviewed": bool(raw.get("reviewed")),
        "from_session": str(raw.get("session_key") or ""),
        "written_at": raw.get("written_at"),
    }


def register(app: FastAPI, settings: Settings, service: RunService, guard: Callable[..., None]) -> None:
    """Mount the library's read and write doors, all behind the bearer token."""

    @app.get("/v1/memory/suggestions", dependencies=[Depends(guard)])
    async def memory_suggestions() -> dict[str, Any]:
        """Facts investigations proposed for the environment memory. Open rows
        wait for a person; the memory itself is never machine-written."""
        rows = suggestions.load(settings.workdir)
        return {"open": [row for row in rows if row.get("status") == "open"]}

    @app.post("/v1/memory/suggestions/{suggestion_id}/accept", dependencies=[Depends(guard)])
    async def memory_suggestion_accept(suggestion_id: str) -> dict[str, Any]:
        row = suggestions.resolve(settings.workdir, suggestion_id, accept=True)
        if row is None:
            raise HTTPException(status_code=404, detail="no such open suggestion")
        return {"accepted": True, "line": row["line"]}

    @app.post("/v1/memory/suggestions/{suggestion_id}/dismiss", dependencies=[Depends(guard)])
    async def memory_suggestion_dismiss(suggestion_id: str) -> dict[str, Any]:
        row = suggestions.resolve(settings.workdir, suggestion_id, accept=False)
        if row is None:
            raise HTTPException(status_code=404, detail="no such open suggestion")
        return {"dismissed": True}

    # Environment memory: {workdir}/CLAUDE.md is loaded into every engine
    # session (setting_sources includes "project"), so facts written here —
    # topology, known false alarms, naming conventions — reach every
    # investigation from its first turn.
    @app.get("/v1/memory", dependencies=[Depends(guard)])
    async def memory_read() -> dict[str, Any]:
        path = settings.workdir / "CLAUDE.md"
        content = ""
        if path.is_file():
            try:
                content = path.read_text(encoding="utf-8")
            except OSError as exc:
                raise HTTPException(status_code=500, detail=f"memory unreadable: {exc}") from exc
        return {"content": content, "path": str(path)}

    @app.put("/v1/memory", dependencies=[Depends(guard)])
    async def memory_write(payload: dict[str, Any]) -> dict[str, Any]:
        raw = _checked_bytes(payload.get("content"), kind="memory")
        atomic_write(settings.workdir / "CLAUDE.md", raw)
        return {"saved": True, "bytes": len(raw)}

    @app.post("/v1/runs/{session_key}/distill", dependencies=[Depends(guard)])
    async def run_distill(session_key: str) -> dict[str, str]:
        """A skill draft for what this run learned — returned, never saved.

        The operator's path: read the draft, prune the dead ends the record
        cannot tell from the useful steps, save it with PUT /v1/skills/{name}.
        The automatic path is HOOKPROBE_AUTO_DISTILL_MAX, which writes the same
        assembly from the service at the end of a run — never through the
        agent's own tools, which cannot reach .claude/ at all. See
        hookprobe.distill for why those two are different acts.
        """
        run = service.get(session_key)
        if run is None:
            raise HTTPException(status_code=404, detail="session not found")
        if not run.finished:
            raise HTTPException(status_code=409, detail="the run is still going")
        return draft_skill(run)

    @app.get("/v1/skills", dependencies=[Depends(guard)])
    async def skills_list() -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for layer, skills_dir in _layers(settings, "skills"):
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
                        # A consolidation draft is waiting for review.
                        "proposal": (entry / "proposal.md").is_file(),
                        # Written by a run rather than installed by a person.
                        # Read from the sidecar, never guessed from the prose:
                        # the page must be able to say which runbooks nobody
                        # has looked at, and that is exactly the claim a
                        # heuristic would get wrong.
                        **_skill_origin(entry),
                    }
                )
        return out

    @app.get("/v1/skills/{name}", dependencies=[Depends(guard)])
    async def skill_detail(name: str) -> dict[str, Any]:
        _require_name(name, kind="skill")
        for layer, skills_dir in _layers(settings, "skills"):
            manifest = skills_dir / name / "SKILL.md"
            if manifest.is_file():
                return {"name": name, "content": manifest.read_text(encoding="utf-8"), "layer": layer}
        raise HTTPException(status_code=404, detail="skill not found")

    @app.get("/v1/skills/{name}/origin", dependencies=[Depends(guard)])
    async def skill_origin_detail(name: str) -> dict[str, Any]:
        """The full provenance record — every revision, not just the last one."""
        _require_name(name, kind="skill")
        path = settings.workdir / ".claude" / "skills" / name / "origin.json"
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"name": name, "revisions": []}
        if not isinstance(record, dict):
            return {"name": name, "revisions": []}
        record["name"] = name
        return record

    @app.get("/v1/skills/{name}/history", dependencies=[Depends(guard)])
    async def skill_history(name: str) -> list[dict[str, Any]]:
        """Every version a write displaced, newest first — the undo the review
        page stands on. Project layer only: nothing else is ever written to."""
        _require_name(name, kind="skill")
        history = settings.workdir / ".claude" / "skills" / name / "history"
        out: list[dict[str, Any]] = []
        if history.is_dir():
            for path in sorted(history.glob("*-SKILL.md"), reverse=True):
                stamp = path.name.split("-", 1)[0]
                if stamp.isdigit():
                    out.append({"stamp": int(stamp), "bytes": path.stat().st_size})
        return out

    @app.get("/v1/skills/{name}/history/{stamp}", dependencies=[Depends(guard)])
    async def skill_history_version(name: str, stamp: int) -> dict[str, Any]:
        _require_name(name, kind="skill")
        path = settings.workdir / ".claude" / "skills" / name / "history" / f"{stamp}-SKILL.md"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="no such version")
        return {"name": name, "stamp": stamp, "content": path.read_text(encoding="utf-8")}

    @app.post("/v1/skills/{name}/review", dependencies=[Depends(guard)])
    async def skill_review(name: str) -> dict[str, Any]:
        """Mark a runbook read without changing a character of it.

        "Reviewed" has only ever meant that somebody looked. Saving an edit
        already flips it; this is for the other outcome of a review — the text
        was fine as the run wrote it — which previously could not be recorded
        at all, so an approved runbook kept its unreviewed badge forever.
        """
        _require_name(name, kind="skill")
        skill_dir = settings.workdir / ".claude" / "skills" / name
        if not (skill_dir / "SKILL.md").is_file():
            raise HTTPException(status_code=404, detail="skill not found")
        record_revision(skill_dir, by="operator", reviewed=True, at=time.time(), detail={"action": "review"})
        return {"reviewed": True, "name": name}

    @app.get("/v1/skills/{name}/proposal", dependencies=[Depends(guard)])
    async def skill_proposal(name: str) -> dict[str, Any]:
        """The consolidation draft awaiting review, beside the manifest it
        would replace. Produced by a consolidation run, written by the service
        — the same one-proposal-at-a-time slot approving or rejecting re-arms."""
        _require_name(name, kind="skill")
        path = settings.workdir / ".claude" / "skills" / name / "proposal.md"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="no proposal")
        return {"name": name, "content": path.read_text(encoding="utf-8")}

    @app.post("/v1/skills/{name}/proposal/approve", dependencies=[Depends(guard)])
    async def skill_proposal_approve(name: str) -> dict[str, Any]:
        """The consolidation becomes the manifest — through the same door every
        write uses: the displaced version is snapshotted first, and approving
        IS the review, so the runbook flips to reviewed."""
        _require_name(name, kind="skill")
        skill_dir = settings.workdir / ".claude" / "skills" / name
        proposal = skill_dir / "proposal.md"
        manifest = skill_dir / "SKILL.md"
        if not proposal.is_file() or not manifest.is_file():
            raise HTTPException(status_code=404, detail="no proposal")
        snapshot(skill_dir, manifest)
        atomic_write(manifest, proposal.read_text(encoding="utf-8").encode("utf-8"))
        proposal.unlink(missing_ok=True)
        record_revision(skill_dir, by="operator", reviewed=True, at=time.time(), detail={"action": "consolidated"})
        return {"approved": True, "name": name}

    @app.post("/v1/skills/{name}/proposal/reject", dependencies=[Depends(guard)])
    async def skill_proposal_reject(name: str) -> dict[str, Any]:
        """Drop the draft, note that somebody did — the manifest is untouched
        and the threshold will re-arm as cases keep arriving."""
        _require_name(name, kind="skill")
        skill_dir = settings.workdir / ".claude" / "skills" / name
        proposal = skill_dir / "proposal.md"
        if not proposal.is_file():
            raise HTTPException(status_code=404, detail="no proposal")
        proposal.unlink(missing_ok=True)
        record_revision(
            skill_dir, by="operator", reviewed=False, at=time.time(), detail={"action": "consolidation-rejected"}
        )
        return {"rejected": True, "name": name}

    @app.put("/v1/skills/{name}", dependencies=[Depends(guard)])
    async def skill_write(name: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Writes always land in the project layer: editing a read-only
        user-layer skill saves a project copy that shadows it from then on."""
        _require_writable_name(name, kind="skill")
        raw = _checked_bytes(payload.get("content"), kind="skill")
        skill_dir = settings.workdir / ".claude" / "skills" / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        manifest = skill_dir / "SKILL.md"
        if manifest.is_file():
            # Same undo an automatic write gets. A correction typed into the
            # wrong runbook is the same accident as a run overwriting a good
            # one, and neither is worth a permission check when the previous
            # version is still on the volume.
            snapshot(skill_dir, manifest)
        atomic_write(manifest, raw)
        # Someone has now read it, which is the only thing "reviewed" means.
        record_revision(skill_dir, by="operator", reviewed=True, at=time.time())
        return {"saved": True, "name": name, "layer": "project", "bytes": len(raw)}

    @app.delete("/v1/skills/{name}", dependencies=[Depends(guard)])
    async def skill_delete(name: str) -> dict[str, Any]:
        """Only the project layer is deletable. Removing a shadow lets the
        user-layer skill of the same name resurface — the host copy was
        never touched."""
        _require_name(name, kind="skill")
        skill_dir = settings.workdir / ".claude" / "skills" / name
        if not (skill_dir / "SKILL.md").is_file():
            for layer, layer_dir in _layers(settings, "skills"):
                if layer != "project" and (layer_dir / name / "SKILL.md").is_file():
                    raise HTTPException(
                        status_code=403, detail="user-layer skills are read-only (mounted from the host)"
                    )
            raise HTTPException(status_code=404, detail="skill not found")
        shutil.rmtree(skill_dir)
        return {"deleted": True, "name": name}

    # Subagent roles: .claude/agents/*.md files in the same layers as skills,
    # plus config-pinned roles from HOOKPROBE_AGENTS_CONFIG. Same copy-on-write
    # editing story as skills — writes land in the project layer, the user
    # layer and the config are never touched from the web.
    @app.get("/v1/agents", dependencies=[Depends(guard)])
    async def agents_list() -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for name, spec in _load_agents_raw(settings.agents_config).items():
            seen.add(name)
            out.append(
                {
                    "name": name,
                    "description": str(spec.get("description") or "")[:200],
                    "source": "config",
                }
            )
        for layer, agents_dir in _layers(settings, "agents"):
            if not agents_dir.is_dir():
                continue
            for entry in sorted(agents_dir.glob("*.md")):
                name = entry.stem
                if name in seen:
                    continue
                try:
                    text = entry.read_text(encoding="utf-8")
                    stat = entry.stat()
                except OSError:
                    continue
                seen.add(name)
                out.append(
                    {
                        "name": name,
                        "description": _skill_description(text),
                        "modified": stat.st_mtime,
                        "source": layer,
                    }
                )
        return out

    @app.get("/v1/agents/{name}", dependencies=[Depends(guard)])
    async def agent_detail(name: str) -> dict[str, Any]:
        _require_name(name, kind="agent")
        config_agents = _load_agents_raw(settings.agents_config)
        if name in config_agents:
            content = json.dumps(config_agents[name], ensure_ascii=False, indent=2)
            return {"name": name, "content": content, "source": "config"}
        for layer, agents_dir in _layers(settings, "agents"):
            path = agents_dir / f"{name}.md"
            if path.is_file():
                return {"name": name, "content": path.read_text(encoding="utf-8"), "source": layer}
        raise HTTPException(status_code=404, detail="agent not found")

    @app.put("/v1/agents/{name}", dependencies=[Depends(guard)])
    async def agent_write(name: str, payload: dict[str, Any]) -> dict[str, Any]:
        _require_writable_name(name, kind="agent")
        if name in _load_agents_raw(settings.agents_config):
            raise HTTPException(status_code=403, detail="config-pinned agents are edited in HOOKPROBE_AGENTS_CONFIG")
        raw = _checked_bytes(payload.get("content"), kind="agent")
        agents_dir = settings.workdir / ".claude" / "agents"
        agents_dir.mkdir(parents=True, exist_ok=True)
        atomic_write(agents_dir / f"{name}.md", raw)
        return {"saved": True, "name": name, "source": "project", "bytes": len(raw)}

    @app.delete("/v1/agents/{name}", dependencies=[Depends(guard)])
    async def agent_delete(name: str) -> dict[str, Any]:
        _require_name(name, kind="agent")
        path = settings.workdir / ".claude" / "agents" / f"{name}.md"
        if not path.is_file():
            if name in _load_agents_raw(settings.agents_config):
                raise HTTPException(status_code=403, detail="config-pinned agents cannot be deleted here")
            for layer, agents_dir in _layers(settings, "agents"):
                if layer != "project" and (agents_dir / f"{name}.md").is_file():
                    raise HTTPException(status_code=403, detail="user-layer agents are read-only")
            raise HTTPException(status_code=404, detail="agent not found")
        path.unlink()
        return {"deleted": True, "name": name}

    # The system prompt append: the same editor story as the environment
    # memory — a file on the volume, hot-read by every run.
    @app.get("/v1/system-prompt", dependencies=[Depends(guard)])
    async def system_prompt_read() -> dict[str, Any]:
        path = system_prompt_path(settings)
        content = ""
        if path.is_file():
            try:
                content = path.read_text(encoding="utf-8")
            except OSError as exc:
                raise HTTPException(status_code=500, detail=f"system prompt unreadable: {exc}") from exc
        return {"content": content, "path": str(path)}

    @app.put("/v1/system-prompt", dependencies=[Depends(guard)])
    async def system_prompt_write(payload: dict[str, Any]) -> dict[str, Any]:
        raw = _checked_bytes(payload.get("content"), kind="system prompt")
        path = system_prompt_path(settings)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, raw)
        return {"saved": True, "bytes": len(raw)}
