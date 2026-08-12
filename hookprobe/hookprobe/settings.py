"""Process configuration from the environment. One flat object, no layers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


@dataclass(frozen=True, slots=True)
class Settings:
    # Inbound: callers authenticate with a single bearer token (an
    # OpenClaw-dialect client presents its hooks token here). Empty = accept
    # anyone, which is a decision for a private compose network, never a
    # default to drift into.
    token: str

    # Engine: which Claude model runs the investigation and how hard the
    # runtime caps it. max_turns is the hard loop budget backing the prompt's
    # soft "≤ 10 tool calls" discipline.
    model: str
    max_turns: int
    max_concurrent: int
    default_timeout_seconds: int
    max_timeout_seconds: int

    # Workspace: one persistent volume. Skills the agent distills live in
    # {workdir}/.claude/skills and survive across runs — that is the point.
    workdir: Path
    mcp_config: Path | None

    # The family loop. event_secret verifies what the pipe delivers to
    # /hooks/event; return_url is where finished investigations from that
    # door report back (the pipe's probe-notify front door), signed with
    # return_secret. escalate_levels is the only judgement the investigator
    # makes about whether an event deserves a paid investigation — the pipe
    # stays content-blind on purpose.
    event_secret: str
    return_url: str
    return_secret: str
    escalate_levels: frozenset[str]

    host: str
    port: int

    @classmethod
    def load(cls) -> Settings:
        mcp = os.environ.get("HOOKPROBE_MCP_CONFIG", "").strip()
        levels = os.environ.get("HOOKPROBE_ESCALATE_LEVELS", "critical,high")
        return cls(
            token=os.environ.get("HOOKPROBE_TOKEN", ""),
            model=os.environ.get("HOOKPROBE_MODEL", "claude-opus-5"),
            max_turns=max(1, _int("HOOKPROBE_MAX_TURNS", 32)),
            max_concurrent=max(1, _int("HOOKPROBE_MAX_CONCURRENT", 2)),
            default_timeout_seconds=max(1, _int("HOOKPROBE_DEFAULT_TIMEOUT_SECONDS", 900)),
            max_timeout_seconds=max(1, _int("HOOKPROBE_MAX_TIMEOUT_SECONDS", 1800)),
            workdir=Path(os.environ.get("HOOKPROBE_WORKDIR", "/data")),
            mcp_config=Path(mcp) if mcp else None,
            event_secret=os.environ.get("HOOKPROBE_EVENT_SECRET", ""),
            return_url=os.environ.get("HOOKPROBE_RETURN_URL", "").strip(),
            return_secret=os.environ.get("HOOKPROBE_RETURN_SECRET", ""),
            escalate_levels=frozenset(part.strip().lower() for part in levels.split(",") if part.strip()),
            host=os.environ.get("HOOKPROBE_HOST", "0.0.0.0"),
            port=_int("HOOKPROBE_PORT", 8088),
        )
