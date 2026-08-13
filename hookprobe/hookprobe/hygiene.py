"""Loop hygiene: stop one run from spinning in place.

Re-running an identical call rarely produces a different answer; it produces the
same answer at the same price. Crossing a repeat threshold appends a note that
tells the agent to change approach or record the gap and move on. The budget
breaker stops spending after the fact — this is the nudge before it costs.

Advisory only: the security boundary stays guard.py plus the read-only
credentials mounted into the container.

Borrowed from DeepSeek Harness (`packages/guard/repeat-tool-reminder`), which
splits the same concern out of its agent loop. Its sibling idea, spilling
oversized tool output to a file, was tried here and removed — the Claude Code
harness already does it (see
.agents/notes/rejected/2026-08-14-tool-output-spill-in-hookprobe.md).
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("hookprobe.hygiene")


class RepeatWatch:
    """Counts identical tool calls within one run and reminds on the threshold."""

    def __init__(self, remind_at: int) -> None:
        self._remind_at = remind_at
        self._seen: dict[str, int] = {}

    def note(self, tool: str, tool_input: Any) -> str | None:
        """Count this call; return a reminder when it crosses the threshold."""
        if self._remind_at <= 0:
            return None
        try:
            payload = json.dumps(tool_input, sort_keys=True, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            payload = repr(tool_input)
        digest = hashlib.sha256(f"{tool}\0{payload}".encode()).hexdigest()
        count = self._seen.get(digest, 0) + 1
        self._seen[digest] = count
        # Remind on the threshold and on every multiple of it, so a persistent
        # loop keeps getting told without every call carrying a note.
        if count < self._remind_at or count % self._remind_at:
            return None
        return (
            f"[hookprobe] That was call #{count} of `{tool or 'this tool'}` with identical arguments in this run. "
            "Repeating it returns the same result at the same cost. Change the approach — a different tool, a "
            "narrower query, another component — or record what stays unknown in `unknowns` / `next_checks` and "
            "move on to the next step of the investigation."
        )


def post_tool_hook(*, session_key: str, repeat_reminder_at: int) -> Callable[..., Any]:
    """PostToolUse hook carrying the repeat reminder."""
    watch = RepeatWatch(repeat_reminder_at)

    async def hook(input_data: dict[str, Any], tool_use_id: str | None, context: Any) -> dict[str, Any]:
        try:
            tool = str(input_data.get("tool_name") or "")
            note = watch.note(tool, input_data.get("tool_input"))
        except Exception:  # noqa: BLE001 — hygiene must never break a run
            logger.debug("post-tool hygiene failed", exc_info=True)
            return {}
        if note is None:
            return {}
        logger.info("repeat reminder for %s in session=%s", tool or "tool", session_key)
        return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": note}}

    return hook
