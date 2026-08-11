"""The engine: one Claude Agent SDK session per analysis run.

hookprobe deliberately owns no agent loop. The Claude Agent SDK brings the
loop, the built-in tools (Bash/Read/Grep/WebSearch/WebFetch/...), the MCP
client and SKILL.md loading; this module only configures a session and
harvests the final text. Swapping engines later means reimplementing one
method.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hookprobe.guard import bash_deny_reason
from hookprobe.settings import Settings

logger = logging.getLogger("hookprobe.engine")

# The agent may write freely inside its own disposable workspace (scratch
# scripts, distilled SKILL.md runbooks). "Read-only" applies to the systems
# it investigates, enforced by the bash guard plus the credentials mounted
# into the container.
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
]


@dataclass(frozen=True, slots=True)
class EngineResult:
    text: str
    message_count: int = 0
    cost_usd: float | None = None
    error: str | None = None
    # The SDK session id — the handle for resuming this investigation later.
    session_id: str | None = None


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


def _load_mcp_servers(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("MCP config %s not loadable (%s); continuing without MCP servers", path, exc)
        return {}
    # Accept both the bare mapping and the .mcp.json wrapper shape.
    if isinstance(raw, dict) and isinstance(raw.get("mcpServers"), dict):
        return raw["mcpServers"]
    return raw if isinstance(raw, dict) else {}


class ClaudeAgentEngine:
    """Runs one unattended analysis per call. No sessions, no resume."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._workdir = settings.workdir
        self._mcp_servers = _load_mcp_servers(settings.mcp_config)
        (self._workdir / ".claude" / "skills").mkdir(parents=True, exist_ok=True)

    async def run(self, *, message: str, session_key: str, resume: str | None = None) -> EngineResult:
        # Imported here so the HTTP service (and its tests) never needs the SDK.
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            HookMatcher,
            ResultMessage,
            TextBlock,
            query,
        )

        options = ClaudeAgentOptions(
            cwd=str(self._workdir),
            model=self._settings.model,
            # Unattended: nobody is there to answer a permission prompt. The
            # boundary is the bash guard plus read-only credentials, not a
            # human in the loop.
            permission_mode="bypassPermissions",
            allowed_tools=_ALLOWED_TOOLS,
            max_turns=self._settings.max_turns,
            # "project" makes the SDK load {workdir}/.claude/skills — the
            # runbooks previous runs distilled.
            setting_sources=["project"],
            mcp_servers=self._mcp_servers,
            hooks={"PreToolUse": [HookMatcher(matcher="Bash", hooks=[_bash_guard_hook])]},
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
            if isinstance(msg, AssistantMessage):
                text = "\n".join(b.text for b in msg.content if isinstance(b, TextBlock) and b.text)
                if text:
                    last_text = text
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
        return EngineResult(
            text=text,
            message_count=message_count,
            cost_usd=getattr(result, "total_cost_usd", None),
            error=error,
            session_id=getattr(result, "session_id", None),
        )
