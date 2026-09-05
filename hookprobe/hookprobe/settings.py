"""Process configuration from the environment. One flat object, no layers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from hookprobe import automation


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


def _endpoint_host(base_url: str) -> str:
    """Host of ANTHROPIC_BASE_URL, or Anthropic's own when it is unset.

    Host only, never the path and never a query: a base URL is not supposed to
    carry a credential, and "not supposed to" is not a reason to render one.
    """
    host = urlsplit(base_url.strip()).hostname if base_url.strip() else ""
    return host or "api.anthropic.com"


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
    # Which MCP tools this instance may actually call, as a closed set —
    # `mcp__chat__chat_search_messages` or `mcp__chat__*` for a whole
    # server. EMPTY DENIES EVERY MCP TOOL, deliberately: mounting a server is
    # an act of plumbing, deciding what the agent may do with it is an act of
    # policy, and this component reads attacker-influenced text, so the two are
    # not allowed to be the same decision. A read-only server does not exist —
    # a chat server ships `send_message` beside `search_chat_records` — and without
    # this, "give it eyes" and "give it a mouth" were one mount. Enforced in
    # engine.py by a PreToolUse hook, the same mechanism as the bash guard, so
    # it does not depend on permission_mode.
    mcp_tools: frozenset[str]

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

    # Where a finished run's report goes when an operator clicks "hand off",
    # and the credential for that one door. Both empty = the button is not
    # offered, which is the shipping default. It points at a PIPE door, never at
    # another node: see handoff.py for why one hop shorter would cost the whole
    # record of the handover.
    handoff_url: str
    # Signs that one delivery, timestamped, exactly like every other hop here.
    handoff_secret: str

    # Which posture the bash guard takes. `readonly` (default) is the
    # investigator's: mutating verbs are refused. `danger-only` is for a runner
    # an operator gave scoped WRITE credentials to — the verbs are allowed and
    # only the handful no credential scope can undo are not. An unknown value
    # falls back to readonly, because a typo in the variable that decides
    # whether a runner may write must fail closed.
    #
    # This is not what bounds such a runner. The credentials mounted into it
    # are; see guard.py for why an allowlist would be the wrong shape here.
    bash_guard: str

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
    # The declared ceiling per class of automation — see automation.py. A
    # class may do at most its tier; defaults preserve today's behaviour, so
    # this is inert until an operator writes a line. The scattered auto-apply
    # knobs (memory_auto_apply here, the remediation allowlist, escalate_levels)
    # operate UNDER it: a knob cannot grant what the tier does not permit.
    automation_tiers: dict[str, str]
    # Severities that may wake a person, as a comma-separated list.
    escalate_levels: frozenset[str]
    # The closed vocabulary this instance is allowed to CONCLUDE with, so a
    # report can steer the next hop instead of only being read. Empty (default)
    # is off. The set is declared by the operator and the agent picks from
    # inside it — never free text, because a routing key decides where money is
    # spent and this is the component that reads attacker-influenced prose. See
    # .agents/notes/implemented/2026-09-04-an-investigator-verdict-may-steer-a-route-from-a-closed-set.md
    verdicts: frozenset[str]

    # The budget breaker, guarding the only path that spends money without a
    # human asking: once the window's recorded spend reaches budget_usd, the
    # event door refuses NEW investigations (each refusal still reports
    # itself through the family loop). 0 disables. Operator-driven doors
    # (/hooks/agent, continue, the UI) are never gated.
    budget_usd: float
    # Window the spend ceiling is measured over.
    budget_window_hours: float
    # Whether the breaker above also refuses NEW sessions on /hooks/agent. The
    # door was declared operator-driven and left ungated — true when a person
    # types the trigger, false on the deployment where a platform fires it from
    # webhooks: that door was the one actually spending, unguarded. Off by
    # default because a human's explicit request should not bounce off a meter;
    # turn it on where the caller is a machine.
    budget_gates_agent_door: bool

    # How long a standing not_worth_it ruling may keep answering a condition
    # from its runbook instead of starting a paid engine run. The weekly patrol
    # refiles rulings it can still defend, so 14 tolerates one missed patrol;
    # 0 turns the gate off entirely.
    ruling_ttl_days: int
    # A ruled-useless condition still gets a REAL investigation this often —
    # the evidence behind a ruling goes stale, and a gate that never re-checks
    # would keep citing last month's case files at this month's incident.
    ruling_reverify_days: int

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
            model_endpoint=_endpoint_host(os.environ.get("ANTHROPIC_BASE_URL", "")),
            max_turns=max(1, _int("HOOKPROBE_MAX_TURNS", 32)),
            max_concurrent=max(1, _int("HOOKPROBE_MAX_CONCURRENT", 2)),
            default_timeout_seconds=max(1, _int("HOOKPROBE_DEFAULT_TIMEOUT_SECONDS", 900)),
            max_timeout_seconds=max(1, _int("HOOKPROBE_MAX_TIMEOUT_SECONDS", 1800)),
            workdir=Path(os.environ.get("HOOKPROBE_WORKDIR", "/data")),
            mcp_config=Path(mcp) if mcp else None,
            mcp_tools=frozenset(
                part.strip() for part in os.environ.get("HOOKPROBE_MCP_TOOLS", "").split(",") if part.strip()
            ),
            setting_sources=sources or ("project",),
            skills=os.environ.get("HOOKPROBE_SKILLS", "").strip(),
            system_prompt_append=_path_env("HOOKPROBE_SYSTEM_PROMPT_APPEND"),
            agents_config=_path_env("HOOKPROBE_AGENTS_CONFIG"),
            repeat_reminder_at=max(0, _int("HOOKPROBE_REPEAT_REMINDER_AT", 3)),
            handoff_url=(os.environ.get("HOOKPROBE_HANDOFF_URL") or "").strip(),
            handoff_secret=os.environ.get("HOOKPROBE_HANDOFF_SECRET") or "",
            bash_guard=(os.environ.get("HOOKPROBE_BASH_GUARD") or "").strip().lower() or "readonly",
            bash_timeout_ms=max(0, _int("HOOKPROBE_BASH_TIMEOUT_MS", 120000)),
            bash_max_timeout_ms=max(0, _int("HOOKPROBE_BASH_MAX_TIMEOUT_MS", 600000)),
            auto_distill_max=max(0, _int("HOOKPROBE_AUTO_DISTILL_MAX", 0)),
            ruling_ttl_days=max(0, _int("HOOKPROBE_RULING_TTL_DAYS", 14)),
            ruling_reverify_days=max(1, _int("HOOKPROBE_RULING_REVERIFY_DAYS", 7)),
            coalesce_window_seconds=max(0, _int("HOOKPROBE_COALESCE_WINDOW_SECONDS", 1800)),
            consolidate_at=max(0, _int("HOOKPROBE_CONSOLIDATE_AT", 5)),
            remediation_allowlist=_path_env("HOOKPROBE_REMEDIATION_ALLOWLIST"),
            event_secret=os.environ.get("HOOKPROBE_EVENT_SECRET", ""),
            return_url=os.environ.get("HOOKPROBE_RETURN_URL", "").strip(),
            return_secret=os.environ.get("HOOKPROBE_RETURN_SECRET", ""),
            ruling_url=os.environ.get("HOOKPROBE_RULING_URL", ""),
            ruling_secret=os.environ.get("HOOKPROBE_RULING_SECRET", ""),
            memory_auto_apply=_flag("HOOKPROBE_MEMORY_AUTO_APPLY", True),
            automation_tiers=automation.parse_tiers(os.environ.get("HOOKPROBE_AUTOMATION_TIERS", "")),
            escalate_levels=frozenset(part.strip().lower() for part in levels.split(",") if part.strip()),
            verdicts=frozenset(
                part.strip().lower() for part in os.environ.get("HOOKPROBE_VERDICTS", "").split(",") if part.strip()
            ),
            budget_usd=max(0.0, _float("HOOKPROBE_BUDGET_USD", 0.0)),
            budget_window_hours=max(0.1, _float("HOOKPROBE_BUDGET_WINDOW_HOURS", 24.0)),
            budget_gates_agent_door=_flag("HOOKPROBE_BUDGET_GATES_AGENT_DOOR", False),
            retention_days=max(0, _int("HOOKPROBE_RETENTION_DAYS", 0)),
            alarm_url=os.environ.get("HOOKPROBE_ALARM_URL", "").strip(),
            alarm_min_interval_seconds=_int("HOOKPROBE_ALARM_MIN_INTERVAL_SECONDS", 600),
            # Bind-all is the right default inside a container; every compose
            # in this repo maps the host side to loopback where it matters.
            host=os.environ.get("HOOKPROBE_HOST", "0.0.0.0"),  # nosec B104
            port=_int("HOOKPROBE_PORT", 8088),
        )

    # WHERE that model name is sent. Host only, derived from ANTHROPIC_BASE_URL.
    #
    # `model` is the name REQUESTED, not the one served, and the SDK cannot tell
    # us the difference: an Anthropic-compatible gateway accepts `claude-opus-5`
    # and maps it to its own model server-side, so `model_usage` keys and their
    # `canonicalModel` both just echo what was sent. Measured on this deployment
    # — 30k input tokens billed under `claude-opus-5` against open.bigmodel.cn.
    #
    # So the honest thing to show beside a model name is its destination, which
    # needs no guessing. Without it a board reading `opus-5` means Anthropic to
    # every reader, and has meant DeepSeek and then BigModel here.
    model_endpoint: str = "api.anthropic.com"
