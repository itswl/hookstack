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
    # Inbound: WebhookWise authenticates with a single bearer token (the value
    # it already holds as OPENCLAW_HOOKS_TOKEN). Empty = accept anyone, which
    # is a decision for a private compose network, never a default to drift
    # into.
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

    host: str
    port: int

    @classmethod
    def load(cls) -> Settings:
        mcp = os.environ.get("HOOKPROBE_MCP_CONFIG", "").strip()
        return cls(
            token=os.environ.get("HOOKPROBE_TOKEN", ""),
            model=os.environ.get("HOOKPROBE_MODEL", "claude-opus-5"),
            max_turns=max(1, _int("HOOKPROBE_MAX_TURNS", 32)),
            max_concurrent=max(1, _int("HOOKPROBE_MAX_CONCURRENT", 2)),
            default_timeout_seconds=max(1, _int("HOOKPROBE_DEFAULT_TIMEOUT_SECONDS", 900)),
            max_timeout_seconds=max(1, _int("HOOKPROBE_MAX_TIMEOUT_SECONDS", 1800)),
            workdir=Path(os.environ.get("HOOKPROBE_WORKDIR", "/data")),
            mcp_config=Path(mcp) if mcp else None,
            host=os.environ.get("HOOKPROBE_HOST", "0.0.0.0"),
            port=_int("HOOKPROBE_PORT", 8088),
        )
