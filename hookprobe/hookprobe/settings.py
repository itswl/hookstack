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


def _flag(name: str, default: bool) -> bool:
    """A boolean env var, with the off switches spelled the way people spell them.

    Unset means `default`, so a knob that ships ON stays on without the operator
    having to know it exists — and anything unrecognised also means `default`,
    because a typo must not quietly flip a security-adjacent behaviour.
    """
    raw = os.environ.get(name, "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    if raw in ("1", "true", "yes", "on"):
        return True
    return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _path_env(name: str) -> Path | None:
    raw = os.environ.get(name, "").strip()
    return Path(raw) if raw else None


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
    # Ceiling on engine turns in one investigation.
    max_turns: int
    # How many investigations may run at once.
    max_concurrent: int
    # Wall-clock budget for an investigation that does not ask for one.
    default_timeout_seconds: int
    # Ceiling on what a caller may ask for.
    max_timeout_seconds: int

    # Workspace: one persistent volume. Skills the agent distills live in
    # {workdir}/.claude/skills and survive across runs — that is the point.
    workdir: Path
    # MCP server config handed to the engine; empty means none.
    mcp_config: Path | None

    # Skill layers. setting_sources picks which filesystem layers the engine
    # loads: "project" = {workdir}/.claude (the distilled runbooks), "user" =
    # $HOME/.claude (a host library mounted read-only at
    # /data/home/.claude/skills). skills filters which discovered skills
    # enter the session ("" = engine default, "all", or comma-separated
    # names) — a mounted library of dozens would flood the context otherwise.
    setting_sources: tuple[str, ...]
    # Directory of skills the engine may load.
    skills: str

    # Operator methodology appended to the engine's own system prompt, read
    # fresh at every run (hot-editable). Unset falls back to the convention
    # path {workdir}/system-prompt.md when that file exists.
    system_prompt_append: Path | None
    # Named subagent roles (JSON: name -> {description, prompt, tools?,
    # model?, skills?}), the config-file twin of .claude/agents/*.md files.
    agents_config: Path | None

    # Loop hygiene (hygiene.py), all advisory — the security boundary stays the
    # bash guard plus read-only credentials.
    #   repeat_reminder_at: after N identical calls, remind the agent to change
    #                       approach. 0 disables.
    #   bash_timeout_ms / bash_max_timeout_ms: per-command deadlines handed to
    #                       the CLI (BASH_DEFAULT_TIMEOUT_MS / BASH_MAX_TIMEOUT_MS)
    #                       so one hung command cannot eat a whole run's wall
    #                       clock. The defaults match the CLI's own, so this is
    #                       a lever to tighten, not a behaviour change; 0 leaves
    #                       the CLI's default in place.
    repeat_reminder_at: int
    # Default timeout for one shell command the engine runs.
    bash_timeout_ms: int
    # Ceiling on what a single command may ask for.
    bash_max_timeout_ms: int

    # Auto-distill: how many runbooks a finished run may leave behind for the
    # next one, written by the service (never by the agent — see
    # hookprobe.inputs). A cap rather than a flag, because the cap is the
    # setting that matters: every runbook is prefix cost on every later run,
    # and there is no reviewer deciding when to stop. 0 keeps the loop manual,
    # which is the right default for a deployment nobody is watching.
    auto_distill_max: int

    # Consolidation: at this many accumulated cases, a runbook triggers one
    # agent run that drafts a curated procedure from the case pile. The draft
    # lands beside the manifest as a PROPOSAL and waits for review — approving
    # it is the operator action that replaces the pile. 0 disables. Each
    # trigger costs one model run, so the threshold is the budget lever.
    consolidate_at: int

    # Remediation executor gate: a file of full-match regexes, one per line,
    # hot-read at execution time. Deny by default — unset or empty means
    # proposals collect and nothing executes, which is the shipping posture.
    remediation_allowlist: Path | None

    # Storm coalescing at the event door: a re-fire of the same alert (same
    # source + title, new event id) within this many seconds continues the
    # existing investigation instead of funding a new one. The judge's reuse
    # route already stops verdict storms; without this, every re-fire that
    # cleared the escalation bar still bought a full cold-start investigation
    # of a condition the previous session had already mapped. 0 disables.
    coalesce_window_seconds: int

    # The family loop. event_secret verifies what the pipe delivers to
    # /hooks/event; return_url is where finished investigations from that
    # door report back (the pipe's probe-notify front door), signed with
    # return_secret. escalate_levels is the only judgement the investigator
    # makes about whether an event deserves a paid investigation — the pipe
    # stays content-blind on purpose.
    event_secret: str
    # The one door findings go back to.
    return_url: str
    # Outbound HMAC secret; signs the finding on its way back.
    return_secret: str
    # Where a retrospective condition ruling goes, and the credential for that
    # ONE door. Empty on either side means the feature is off and a patrol that
    # rules gets its markers stripped with nothing sent — see hookprobe.rulings
    # for why the agent does not post this itself.
    ruling_url: str
    # Signs the rulings this service posts to the judge.
    ruling_secret: str
    # Apply a suggested environment fact without waiting for a person, when its
    # SHAPE cannot act (hookprobe.suggestions._UNSAFE). On by default: the queue
    # was a dead end on a deployment where nobody answers, and everything the
    # check objects to still waits for a human.
    memory_auto_apply: bool
    # Severities that may wake a person, as a comma-separated list.
    escalate_levels: frozenset[str]

    # The budget breaker, guarding the only path that spends money without a
    # human asking: once the window's recorded spend reaches budget_usd, the
    # event door refuses NEW investigations (each refusal still reports
    # itself through the family loop). 0 disables. Operator-driven doors
    # (/hooks/agent, continue, the UI) are never gated.
    budget_usd: float
    # Window the spend ceiling is measured over.
    budget_window_hours: float

    # Volume retention (days): case files and engine transcripts older than
    # this are pruned daily. 0 keeps everything — the case files are the
    # agent's episodic memory, so deletion is a choice, never a surprise.
    retention_days: int

    # Direct self-alarm for report returns that exhaust their retries: the
    # pipe is the broken link at that moment, so this posts straight to a
    # bot/collector URL, touching nothing on the path that failed.
    alarm_url: str
    # Floor between two alarm sends, so a storm cannot page repeatedly.
    alarm_min_interval_seconds: int

    # Address the service binds to.
    host: str
    # Port the service listens on.
    port: int

    @classmethod
    def load(cls) -> Settings:
        mcp = os.environ.get("HOOKPROBE_MCP_CONFIG", "").strip()
        levels = os.environ.get("HOOKPROBE_ESCALATE_LEVELS", "critical,high")
        sources = tuple(
            part.strip()
            for part in os.environ.get("HOOKPROBE_SETTING_SOURCES", "project").split(",")
            if part.strip() in ("user", "project", "local")
        )
        return cls(
            token=os.environ.get("HOOKPROBE_TOKEN", ""),
            model=os.environ.get("HOOKPROBE_MODEL", "claude-opus-5"),
            max_turns=max(1, _int("HOOKPROBE_MAX_TURNS", 32)),
            max_concurrent=max(1, _int("HOOKPROBE_MAX_CONCURRENT", 2)),
            default_timeout_seconds=max(1, _int("HOOKPROBE_DEFAULT_TIMEOUT_SECONDS", 900)),
            max_timeout_seconds=max(1, _int("HOOKPROBE_MAX_TIMEOUT_SECONDS", 1800)),
            workdir=Path(os.environ.get("HOOKPROBE_WORKDIR", "/data")),
            mcp_config=Path(mcp) if mcp else None,
            setting_sources=sources or ("project",),
            skills=os.environ.get("HOOKPROBE_SKILLS", "").strip(),
            system_prompt_append=_path_env("HOOKPROBE_SYSTEM_PROMPT_APPEND"),
            agents_config=_path_env("HOOKPROBE_AGENTS_CONFIG"),
            repeat_reminder_at=max(0, _int("HOOKPROBE_REPEAT_REMINDER_AT", 3)),
            bash_timeout_ms=max(0, _int("HOOKPROBE_BASH_TIMEOUT_MS", 120000)),
            bash_max_timeout_ms=max(0, _int("HOOKPROBE_BASH_MAX_TIMEOUT_MS", 600000)),
            auto_distill_max=max(0, _int("HOOKPROBE_AUTO_DISTILL_MAX", 0)),
            coalesce_window_seconds=max(0, _int("HOOKPROBE_COALESCE_WINDOW_SECONDS", 1800)),
            consolidate_at=max(0, _int("HOOKPROBE_CONSOLIDATE_AT", 5)),
            remediation_allowlist=_path_env("HOOKPROBE_REMEDIATION_ALLOWLIST"),
            event_secret=os.environ.get("HOOKPROBE_EVENT_SECRET", ""),
            return_url=os.environ.get("HOOKPROBE_RETURN_URL", "").strip(),
            return_secret=os.environ.get("HOOKPROBE_RETURN_SECRET", ""),
            ruling_url=os.environ.get("HOOKPROBE_RULING_URL", ""),
            ruling_secret=os.environ.get("HOOKPROBE_RULING_SECRET", ""),
            memory_auto_apply=_flag("HOOKPROBE_MEMORY_AUTO_APPLY", True),
            escalate_levels=frozenset(part.strip().lower() for part in levels.split(",") if part.strip()),
            budget_usd=max(0.0, _float("HOOKPROBE_BUDGET_USD", 0.0)),
            budget_window_hours=max(0.1, _float("HOOKPROBE_BUDGET_WINDOW_HOURS", 24.0)),
            retention_days=max(0, _int("HOOKPROBE_RETENTION_DAYS", 0)),
            alarm_url=os.environ.get("HOOKPROBE_ALARM_URL", "").strip(),
            alarm_min_interval_seconds=_int("HOOKPROBE_ALARM_MIN_INTERVAL_SECONDS", 600),
            # Bind-all is the right default inside a container; every compose
            # in this repo maps the host side to loopback where it matters.
            host=os.environ.get("HOOKPROBE_HOST", "0.0.0.0"),  # nosec B104
            port=_int("HOOKPROBE_PORT", 8088),
        )
