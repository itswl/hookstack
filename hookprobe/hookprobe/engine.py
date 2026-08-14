"""The engine: one Claude Agent SDK session per analysis run.

hookprobe deliberately owns no agent loop. The Claude Agent SDK brings the
loop, the built-in tools (Bash/Read/Grep/WebSearch/WebFetch/...), the MCP
client and SKILL.md loading; this module only configures a session and
harvests the final text. Swapping engines later means reimplementing one
method.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hookprobe.guard import bash_deny_reason
from hookprobe.hygiene import post_tool_hook
from hookprobe.settings import Settings

logger = logging.getLogger("hookprobe.engine")

# The agent may write freely inside its own disposable workspace (scratch
# scripts, distilled SKILL.md runbooks). "Read-only" applies to the systems
# it investigates, enforced by the bash guard plus the credentials mounted
# into the container. Task enables parallel sub-investigations for cascading
# incidents; hooks (and so the bash guard) apply inside subagents too.
_ALLOWED_TOOLS = [
    "Bash",
    "Read",
    "Write",
    "Edit",
    "Glob",
    "Grep",
    "WebSearch",
    "WebFetch",
    "TodoWrite",
    "Skill",
    "Task",
    "Agent",  # the CLI's current name for the subagent tool; keep both spellings
]


@dataclass(frozen=True, slots=True)
class EngineResult:
    text: str
    message_count: int = 0
    cost_usd: float | None = None
    error: str | None = None
    # The SDK session id — the handle for resuming this investigation later.
    session_id: str | None = None
    # Raw accounting from the engine, stored as-is: token usage for the turn,
    # the per-model breakdown (whose keys name the models that actually ran),
    # and wall-clock duration.
    usage: dict[str, Any] | None = None
    model_usage: dict[str, Any] | None = None
    duration_ms: int | None = None


async def _bash_guard_hook(input_data: dict[str, Any], tool_use_id: str | None, context: Any) -> dict[str, Any]:
    command = str((input_data.get("tool_input") or {}).get("command") or "")
    reason = bash_deny_reason(command)
    if reason is None:
        return {}
    logger.warning("bash command denied: %s", command)
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def _tool_detail(tool_input: Any) -> str:
    """One line saying what a tool call is about, for the live process feed."""
    data = tool_input if isinstance(tool_input, dict) else {}
    for key in ("command", "file_path", "pattern", "query", "url", "path", "skill", "description"):
        value = data.get(key)
        if value:
            return str(value)[:300]
    try:
        return json.dumps(data, ensure_ascii=False)[:200]
    except (TypeError, ValueError):
        return ""


def _skills_filter(raw: str) -> list[str] | str | None:
    """HOOKPROBE_SKILLS → the SDK's `skills` option.

    "" keeps the engine's own default listing; "all" enables every discovered
    skill; a comma list pins the session to exactly those names. This is a
    context filter, not a sandbox — unlisted skills stay on disk.
    """
    if not raw:
        return None
    if raw == "all":
        return "all"
    return [part.strip() for part in raw.split(",") if part.strip()]


def _load_mcp_servers(path: Path | None, include_disabled: bool = False) -> dict[str, Any]:
    """Read the MCP config fresh — called per run, so edits apply without a
    restart. Three dialects are accepted: the bare {name: spec} mapping, the
    .mcp.json wrapper ({"mcpServers": {...}}), and the marketplace config.json
    shape whose specs carry an `enabled` flag (false = skip, and the flag
    itself is stripped before the SDK sees it). include_disabled keeps the
    skipped entries WITH their flag — for the browser, never for the engine."""
    if path is None:
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("MCP config %s not loadable (%s); continuing without MCP servers", path, exc)
        return {}
    if isinstance(raw, dict) and isinstance(raw.get("mcpServers"), dict):
        raw = raw["mcpServers"]
    if not isinstance(raw, dict):
        return {}
    servers: dict[str, Any] = {}
    for name, spec in raw.items():
        if isinstance(spec, dict):
            if spec.get("enabled") is False and not include_disabled:
                continue
            if not include_disabled:
                spec = {key: value for key, value in spec.items() if key != "enabled"}
        servers[str(name)] = spec
    return servers


def _load_agents_raw(path: Path | None) -> dict[str, dict[str, Any]]:
    """HOOKPROBE_AGENTS_CONFIG: named subagent roles as plain JSON.

    {name: {description, prompt, tools?, model?, skills?}} — the config-file
    twin of .claude/agents/*.md files, for roles an operator wants pinned in
    deployment config rather than on the volume. Invalid entries are dropped
    with a warning; the investigation must not die of a bad role file.
    """
    if path is None:
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("agents config %s not loadable (%s); continuing without custom agents", path, exc)
        return {}
    if not isinstance(raw, dict):
        return {}
    agents: dict[str, dict[str, Any]] = {}
    for name, spec in raw.items():
        if not (isinstance(spec, dict) and spec.get("description") and spec.get("prompt")):
            logger.warning("agents config: %r needs description and prompt; dropped", name)
            continue
        agents[str(name)] = {
            key: spec[key] for key in ("description", "prompt", "tools", "model", "skills") if key in spec
        }
    return agents


def _system_prompt_append(settings: Settings) -> str:
    """Operator methodology, read fresh each run so edits apply immediately.

    The configured path wins; otherwise the convention path
    {workdir}/system-prompt.md applies when it exists. Empty means "engine
    default prompt only"."""
    path = settings.system_prompt_append or (settings.workdir / "system-prompt.md")
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _audit_hook(audit_dir: Path, session_key: str) -> Callable[..., Any]:
    """PostToolUse flight recorder: one JSONL line per tool call, per day.

    The run's own event feed is capped and lives on the run record; this is
    the uncapped, greppable account across ALL runs — who ran what, when,
    for which session. Append-only, never raises, pruned by retention."""

    async def hook(input_data: dict[str, Any], tool_use_id: str | None, context: Any) -> dict[str, Any]:
        try:
            import time

            response = input_data.get("tool_response")
            line = {
                "ts": round(time.time(), 3),
                "session": session_key,
                "tool": str(input_data.get("tool_name") or ""),
                "detail": _tool_detail(input_data.get("tool_input")),
                "error": bool(response.get("is_error")) if isinstance(response, dict) else False,
            }
            audit_dir.mkdir(parents=True, exist_ok=True)
            day_file = audit_dir / (time.strftime("%Y-%m-%d") + ".jsonl")
            with day_file.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(line, ensure_ascii=False) + "\n")
        except Exception:  # noqa: BLE001 — the recorder must never break the run
            logger.debug("audit write failed", exc_info=True)
        return {}

    return hook


def _file_fact(path: Path) -> dict[str, Any] | None:
    """Size and content digest of a prompt input, or None when absent."""
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    return {
        "path": str(path),
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest()[:12],
    }


def _skill_names(root: Path, limit: int = 40) -> list[str]:
    try:
        names = sorted(p.parent.name for p in root.glob("*/SKILL.md"))
    except OSError:
        return []
    return names[:limit]


def _agent_names(root: Path, limit: int = 40) -> list[str]:
    try:
        names = sorted(p.stem for p in root.glob("*.md"))
    except OSError:
        return []
    return names[:limit]


class ClaudeAgentEngine:
    """Runs one unattended analysis per call. No sessions, no resume."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._workdir = settings.workdir
        self._agents_raw = _load_agents_raw(settings.agents_config)
        (self._workdir / ".claude" / "skills").mkdir(parents=True, exist_ok=True)

    def _engine_env(self) -> dict[str, str]:
        """Per-command deadlines, armed as deployment policy.

        A single hung command — a `curl` at an unreachable host, a `kubectl` at a
        wedged API server — otherwise holds a slot until the whole run times out,
        spending the run's remaining turns on nothing.
        """
        env: dict[str, str] = {}
        if self._settings.bash_timeout_ms > 0:
            env["BASH_DEFAULT_TIMEOUT_MS"] = str(self._settings.bash_timeout_ms)
        if self._settings.bash_max_timeout_ms > 0:
            env["BASH_MAX_TIMEOUT_MS"] = str(self._settings.bash_max_timeout_ms)
        return env

    def describe_inputs(self, *, resume: str | None = None) -> dict[str, Any]:
        """What this run will actually put in front of the model.

        Model-visible means recorded. The prompt is assembled from files on a
        mutable volume — the environment memory, the skills previous runs
        distilled, subagent roles, an appended methodology — so a report is only
        explainable later if the run wrote down which of them were in force. A
        stale line in the memory file once made every report come back in the
        wrong language while the request itself looked identical; this record is
        what would have shown it in one glance.

        Digests, not contents: enough to prove which text was loaded without
        copying investigation instructions into every result file.
        """
        home = Path(os.environ.get("HOME", "") or "/data/home")
        skills: dict[str, Any] = {"filter": self._settings.skills or "(engine default)"}
        if "project" in self._settings.setting_sources:
            skills["project"] = _skill_names(self._workdir / ".claude" / "skills")
        if "user" in self._settings.setting_sources:
            skills["user"] = _skill_names(home / ".claude" / "skills")
        return {
            "model": self._settings.model,
            "max_turns": self._settings.max_turns,
            "setting_sources": list(self._settings.setting_sources),
            "skills": skills,
            "agents": {
                "config": sorted(self._agents_raw),
                "files": _agent_names(self._workdir / ".claude" / "agents"),
            },
            "system_prompt_append": _file_fact(
                self._settings.system_prompt_append or (self._workdir / "system-prompt.md")
            ),
            "memory": _file_fact(self._workdir / "CLAUDE.md"),
            "mcp_servers": sorted(_load_mcp_servers(self._settings.mcp_config)),
            "resumed": bool(resume),
            "hygiene": {
                "repeat_reminder_at": self._settings.repeat_reminder_at,
                "bash_timeout_ms": self._settings.bash_timeout_ms,
                "bash_max_timeout_ms": self._settings.bash_max_timeout_ms,
            },
        }

    async def run(
        self,
        *,
        message: str,
        session_key: str,
        resume: str | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> EngineResult:
        # Imported here so the HTTP service (and its tests) never needs the SDK.
        from claude_agent_sdk import (
            AgentDefinition,
            AssistantMessage,
            ClaudeAgentOptions,
            HookMatcher,
            ResultMessage,
            StreamEvent,
            TextBlock,
            ToolUseBlock,
            query,
        )

        def emit(event: dict[str, Any]) -> None:
            if on_event is None:
                return
            try:
                on_event(event)
            except Exception:  # noqa: BLE001 — a broken observer must not kill the run
                logger.exception("on_event callback failed")

        append = _system_prompt_append(self._settings)
        options = ClaudeAgentOptions(
            cwd=str(self._workdir),
            model=self._settings.model,
            # Unattended: nobody is there to answer a permission prompt. The
            # boundary is the bash guard plus read-only credentials, not a
            # human in the loop.
            permission_mode="bypassPermissions",
            # Partial messages turn the answer into a stream of deltas instead of
            # one block at the end. Watching an investigation is most of what the
            # console is for, and an agent that says nothing for two minutes and
            # then everything at once is indistinguishable from a hung one.
            include_partial_messages=True,
            allowed_tools=_ALLOWED_TOOLS,
            max_turns=self._settings.max_turns,
            # Keep the engine's own system prompt; append the operator's
            # methodology when a system-prompt file is present.
            system_prompt=({"type": "preset", "preset": "claude_code", "append": append} if append else None),
            # "project" loads {workdir}/.claude/skills — the runbooks previous
            # runs distilled. Adding "user" (HOOKPROBE_SETTING_SOURCES) loads
            # $HOME/.claude too — a host skills library mounted read-only.
            setting_sources=list(self._settings.setting_sources),
            skills=_skills_filter(self._settings.skills),
            # Named roles from config, on top of any .claude/agents/*.md files.
            agents=(
                {name: AgentDefinition(**spec) for name, spec in self._agents_raw.items()} if self._agents_raw else None
            ),
            # Read fresh per run: edit the file, the next run uses it.
            mcp_servers=_load_mcp_servers(self._settings.mcp_config),
            hooks={
                "PreToolUse": [HookMatcher(matcher="Bash", hooks=[_bash_guard_hook])],
                # The flight recorder: every tool call, every run, one JSONL
                # line — subagents included, since hooks apply inside them.
                # Alongside it, loop hygiene: notice repeated identical calls.
                "PostToolUse": [
                    HookMatcher(matcher=None, hooks=[_audit_hook(self._workdir / "audit", session_key)]),
                    HookMatcher(
                        matcher=None,
                        hooks=[
                            post_tool_hook(
                                session_key=session_key,
                                repeat_reminder_at=self._settings.repeat_reminder_at,
                            )
                        ],
                    ),
                ],
            },
            # Per-command deadlines; see _engine_env.
            env=self._engine_env(),
            # Follow-up turns reopen the original investigation with its full
            # context (transcripts live under $HOME/.claude — keep that on the
            # persistent volume).
            resume=resume,
        )

        logger.info("engine start session=%s model=%s resume=%s", session_key, self._settings.model, resume or "-")
        last_text = ""
        message_count = 0
        result: Any = None
        async for msg in query(prompt=message, options=options):
            message_count += 1
            if isinstance(msg, StreamEvent):
                # Transient by design: deltas are for a human watching right now.
                # The finished blocks below are what gets recorded.
                if msg.event.get("type") == "content_block_delta":
                    delta = msg.event.get("delta") or {}
                    chunk = delta.get("text") or delta.get("thinking") or ""
                    if chunk:
                        emit(
                            {
                                "type": "delta",
                                "kind": "thinking" if delta.get("type") == "thinking_delta" else "text",
                                "text": chunk,
                            }
                        )
                continue
            if isinstance(msg, AssistantMessage):
                text_parts: list[str] = []
                for block in msg.content:
                    if isinstance(block, TextBlock) and block.text:
                        text_parts.append(block.text)
                        emit({"type": "text", "text": block.text[:500]})
                    elif isinstance(block, ToolUseBlock):
                        event: dict[str, Any] = {
                            "type": "tool_use",
                            "name": block.name,
                            "detail": _tool_detail(block.input),
                        }
                        # The plan checklist is worth keeping structured — the
                        # UI renders it as a live todo list.
                        if block.name == "TodoWrite" and isinstance(block.input, dict):
                            todos = block.input.get("todos")
                            if isinstance(todos, list):
                                event["todos"] = todos[:20]
                        emit(event)
                if text_parts:
                    last_text = "\n".join(text_parts)
            elif isinstance(msg, ResultMessage):
                result = msg

        if result is None:
            return EngineResult(text=last_text, message_count=message_count, error="engine produced no result message")

        text = str(getattr(result, "result", None) or last_text or "").strip()
        error: str | None = None
        if getattr(result, "is_error", False):
            error = f"engine reported {getattr(result, 'subtype', 'error')}"
        elif not text:
            error = "engine returned an empty result"
        usage = getattr(result, "usage", None)
        model_usage = getattr(result, "model_usage", None)
        return EngineResult(
            text=text,
            message_count=message_count,
            cost_usd=getattr(result, "total_cost_usd", None),
            error=error,
            session_id=getattr(result, "session_id", None),
            usage=dict(usage) if isinstance(usage, dict) else None,
            model_usage=dict(model_usage) if isinstance(model_usage, dict) else None,
            duration_ms=getattr(result, "duration_ms", None),
        )
