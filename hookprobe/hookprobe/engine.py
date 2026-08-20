"""The engine: one Claude Agent SDK session per analysis run.

hookprobe deliberately owns no agent loop. The Claude Agent SDK brings the
loop, the built-in tools (Bash/Read/Grep/WebSearch/WebFetch/...), the MCP
client and SKILL.md loading; this module only configures a session and
harvests the final text. Swapping engines later means reimplementing one
method.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from hookprobe import inputs
from hookprobe.files import system_prompt_path
from hookprobe.guard import bash_deny_reason
from hookprobe.hygiene import post_tool_hook
from hookprobe.redact import redact
from hookprobe.settings import Settings

logger = logging.getLogger("hookprobe.engine")

# The agent may write freely inside its workspace — scratch scripts, working
# notes — with one carve-out: not the files that steer the next run. See
# hookprobe.inputs for why a run installing its own runbook is a persistence
# vector rather than a feature. "Read-only" applies to the systems it
# investigates, enforced by the bash guard plus the credentials mounted into
# the container. Task enables parallel sub-investigations for cascading
# incidents; hooks (and so both guards) apply inside subagents too.
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
    # Files on the run's own input surface that changed while it ran. Empty on
    # a healthy run; anything here means the run rewrote what steers the next
    # one, whichever tool it went through. See hookprobe.inputs.
    input_changes: tuple[str, ...] = ()


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


# Tools that put bytes on disk. NotebookEdit and MultiEdit are not in
# _ALLOWED_TOOLS today; naming them costs nothing and means enabling one later
# cannot quietly reopen the hole.
_WRITE_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})
_WRITE_PATH_KEYS = ("file_path", "notebook_path", "path")


def _input_guard_hook(workdir: Path, home: Path | None) -> Callable[..., Any]:
    """PreToolUse: refuse a write aimed at the files that steer the next run."""

    async def hook(input_data: dict[str, Any], tool_use_id: str | None, context: Any) -> dict[str, Any]:
        if str(input_data.get("tool_name") or "") not in _WRITE_TOOLS:
            return {}
        tool_input = input_data.get("tool_input")
        data = tool_input if isinstance(tool_input, dict) else {}
        for key in _WRITE_PATH_KEYS:
            reason = inputs.write_deny_reason(str(data.get(key) or ""), workdir=workdir, home=home)
            if reason is not None:
                logger.warning("input guard denied write: %s", data.get(key))
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": reason,
                    }
                }
        return {}

    return hook


def _hook_list(*fns: Callable[..., Any]) -> list[Any]:
    """The SDK types hook inputs as a TypedDict union; ours are plain dicts on
    purpose — the tests never import the SDK, so its types cannot appear in
    signatures. One list[Any] at the exact boundary, instead of ignores at
    every registration site."""
    return list(fns)


def _tool_detail(tool_input: Any) -> str:
    """One line saying what a tool call is about, for the live process feed.

    Redacted HERE rather than at the sinks, because this one string is the most
    copied in the service: it reaches the run's event feed and `results/*.json`,
    the flight recorder's `audit/*.jsonl`, and — via distill — the case block of
    a generated SKILL.md that every later run loads and /v1/skills serves. Three
    sinks today and a fourth one feature away; masking at each of them is
    masking the next one leaks around. See hookprobe/redact.py for what it does
    and does not catch.
    """
    data = tool_input if isinstance(tool_input, dict) else {}
    for key in ("command", "file_path", "pattern", "query", "url", "path", "skill", "description"):
        value = data.get(key)
        if value:
            return redact(str(value))[:300]
    try:
        return redact(json.dumps(data, ensure_ascii=False))[:200]
    except (TypeError, ValueError):
        return ""


def _skills_filter(raw: str) -> list[str] | Literal["all"] | None:
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
    path = system_prompt_path(settings)
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


def file_fact(path: Path) -> dict[str, Any] | None:
    """Size and content digest of a prompt input, or None when absent.

    Used twice over: once by a run to record what it was given, and once by the
    read path to describe the same file as it stands now. Both sides must hash
    identically for the comparison to mean anything, so they share this.
    """
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


def engine_error(result: Any, text: str) -> str | None:
    """Why a finished run failed, in words the operator can act on.

    `subtype` is the SDK's own label and it can read "success" on a result
    already flagged as an error. The first real patrol run hit exactly that: the
    engine had said `API Error: 402 Insufficient Balance` and the operator was
    told `engine reported success` — a sentence with no information in it and a
    contradiction on its face, while the actual reason sat one field away.

    So report what the engine SAID, and fall back to the subtype only when it
    said nothing at all. Collapsed to one line and capped, because this lands in
    a log line and in `reason=` on the board.
    """
    if getattr(result, "is_error", False):
        detail = " ".join(text.split())[:200]
        return detail or f"engine reported {getattr(result, 'subtype', 'error')}"
    if not text:
        return "engine returned an empty result"
    return None


class ClaudeAgentEngine:
    """Runs one unattended analysis per call. No sessions, no resume."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._workdir = settings.workdir
        self._home = Path(os.environ.get("HOME", "") or "/data/home")
        self._agents_raw = _load_agents_raw(settings.agents_config)
        (self._workdir / ".claude" / "skills").mkdir(parents=True, exist_ok=True)
        # Set for the duration of one run, so a caller that wants the turn to
        # stop can ask the SDK rather than kill the coroutine. None between runs
        # and after one finishes — an engine instance runs one turn at a time.
        self._interrupt: Callable[[], Any] | None = None

    async def stop(self) -> bool:
        """Ask the running turn to wind down, keeping its ResultMessage.

        This is what makes a stop accountable. Cancelling the coroutine — the
        only option query() left — discarded the SDK's final message, and with it
        the cost of a run the provider had already billed. An interrupt lets the
        turn end on its own terms, so the bill, the stop_reason and the
        terminal_reason all still arrive.

        False means there was nothing to interrupt, which the caller needs in
        order to fall back to cancelling: a turn that has not reached the SDK yet
        cannot be interrupted through it.
        """
        interrupt = self._interrupt
        if interrupt is None:
            return False
        try:
            await interrupt()
        except Exception:  # noqa: BLE001 — a failed interrupt falls back to cancellation
            logger.exception("interrupt failed; the caller will cancel instead")
            return False
        return True

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
        skills: dict[str, Any] = {"filter": self._settings.skills or "(engine default)"}
        if "project" in self._settings.setting_sources:
            skills["project"] = _skill_names(self._workdir / ".claude" / "skills")
        if "user" in self._settings.setting_sources:
            skills["user"] = _skill_names(self._home / ".claude" / "skills")
        return {
            "model": self._settings.model,
            "max_turns": self._settings.max_turns,
            "setting_sources": list(self._settings.setting_sources),
            "skills": skills,
            "agents": {
                "config": sorted(self._agents_raw),
                "files": _agent_names(self._workdir / ".claude" / "agents"),
            },
            "system_prompt_append": file_fact(system_prompt_path(self._settings)),
            "memory": file_fact(self._workdir / "CLAUDE.md"),
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
            ClaudeSDKClient,
            HookMatcher,
            ResultMessage,
            StreamEvent,
            TextBlock,
            ToolUseBlock,
        )

        def emit(event: dict[str, Any]) -> None:
            if on_event is None:
                return
            try:
                on_event(event)
            except Exception:  # noqa: BLE001 — a broken observer must not kill the run
                logger.exception("on_event callback failed")

        # Step timing, from the hooks rather than the message stream: hooks fire
        # inside subagents too, so this is also the only place a subagent's tool
        # calls surface at all — the message stream carries just the parent's.
        # The pair reports under the tool_use_id; the service matches it to the
        # streamed step, and an id it has never seen is, by elimination, a
        # subagent's.
        step_starts: dict[str, float] = {}

        async def _step_begin(input_data: dict[str, Any], tool_use_id: str | None, context: Any) -> dict[str, Any]:
            if tool_use_id:
                step_starts[tool_use_id] = time.monotonic()
            return {}

        async def _step_done(input_data: dict[str, Any], tool_use_id: str | None, context: Any) -> dict[str, Any]:
            began = step_starts.pop(tool_use_id, None) if tool_use_id else None
            done: dict[str, Any] = {
                "type": "tool_done",
                "id": tool_use_id,
                "name": str(input_data.get("tool_name") or ""),
                "detail": _tool_detail(input_data.get("tool_input")),
            }
            if began is not None:
                done["ms"] = int((time.monotonic() - began) * 1000)
            response = input_data.get("tool_response")
            if isinstance(response, dict) and response.get("is_error"):
                done["error"] = True
            emit(done)
            return {}

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
            setting_sources=cast(Any, list(self._settings.setting_sources)),
            skills=_skills_filter(self._settings.skills),
            # Named roles from config, on top of any .claude/agents/*.md files.
            agents=(
                {name: AgentDefinition(**spec) for name, spec in self._agents_raw.items()} if self._agents_raw else None
            ),
            # Read fresh per run: edit the file, the next run uses it.
            mcp_servers=_load_mcp_servers(self._settings.mcp_config),
            hooks={
                "PreToolUse": [
                    HookMatcher(matcher="Bash", hooks=_hook_list(_bash_guard_hook)),
                    # Matched on every tool, filtered by name inside: the guard
                    # must not depend on how the SDK interprets a matcher
                    # pattern for the one thing it exists to stop.
                    HookMatcher(matcher=None, hooks=_hook_list(_input_guard_hook(self._workdir, self._home))),
                    HookMatcher(matcher=None, hooks=_hook_list(_step_begin)),
                ],
                # The flight recorder: every tool call, every run, one JSONL
                # line — subagents included, since hooks apply inside them.
                # Alongside it, loop hygiene: notice repeated identical calls.
                "PostToolUse": [
                    HookMatcher(matcher=None, hooks=_hook_list(_step_done)),
                    HookMatcher(matcher=None, hooks=_hook_list(_audit_hook(self._workdir / "audit", session_key))),
                    HookMatcher(
                        matcher=None,
                        hooks=_hook_list(
                            post_tool_hook(
                                session_key=session_key,
                                repeat_reminder_at=self._settings.repeat_reminder_at,
                            )
                        ),
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
        inputs_before = inputs.fingerprint(self._workdir, self._home)
        input_changes: tuple[str, ...] = ()
        last_text = ""
        message_count = 0
        result: Any = None
        # ClaudeSDKClient rather than query(), and the reason is the bill.
        #
        # query() is a one-shot async generator: the only way to stop it early is
        # to cancel the coroutine from outside, which is what a wall-clock
        # timeout, the operator's Stop button and a deploy restart all did. The
        # SDK reports dollars only on the final ResultMessage, so cancelling
        # mid-stream threw that message away and the turn recorded cost None —
        # "nobody counted" — for a run the provider had already billed in full.
        #
        # The client can be told to stop instead of being killed. interrupt()
        # makes the SDK wind the turn down and still emit its ResultMessage, so
        # the cost, the stop_reason and the terminal_reason all survive. That is
        # the whole reason for this shape; the message handling below is
        # unchanged.
        client = ClaudeSDKClient(options=options)
        self._interrupt = client.interrupt
        try:
            await client.connect()
            await client.query(message)
            async for msg in client.receive_response():
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
                                # The handle the PostToolUse timer reports back
                                # under, so a duration can find its step.
                                "id": block.id,
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
        finally:
            self._interrupt = None
            # disconnect() cancels any SDK MCP tool still running and gives each
            # server a short grace period, so it is the counterpart to connect()
            # and not optional — a client left open holds the CLI subprocess.
            with contextlib.suppress(Exception):
                await client.disconnect()
            # In a finally because a run that rewrites its own inputs and
            # then fails is exactly the case a return-path check misses,
            # and the next run cannot see it: its own "before" snapshot
            # already contains the change.
            input_changes = self._input_changes(inputs_before)

        if result is None:
            return EngineResult(
                text=last_text,
                message_count=message_count,
                error="engine produced no result message",
                input_changes=input_changes,
            )

        text = str(getattr(result, "result", None) or last_text or "").strip()
        error = engine_error(result, text)
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
            input_changes=input_changes,
        )

    def _input_changes(self, before: dict[str, str]) -> tuple[str, ...]:
        """What this run did to its own input surface — empty when it behaved."""
        found = tuple(inputs.changes(before, inputs.fingerprint(self._workdir, self._home)))
        if found:
            logger.warning("run rewrote its own inputs: %s", "; ".join(found))
        return found
