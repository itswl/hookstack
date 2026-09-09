"""The engine: one Claude Agent SDK session per analysis run.

hookprobe deliberately owns no agent loop. The Claude Agent SDK brings the
loop, the built-in tools (Bash/Read/Grep/WebSearch/WebFetch/...), the MCP
client and SKILL.md loading; this module only configures a session and
harvests the final text. Swapping engines later means reimplementing one
method.
"""

from __future__ import annotations

import asyncio
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

from hookprobe import inputs, telemetry
from hookprobe.files import system_prompt_path
from hookprobe.gate import (
    _WRITE_PATH_KEYS,
    SECRETS_WITHHELD_FROM_AGENT,
    mcp_deny_reason,
    shell_write_target,
    trace_environment,
)
from hookprobe.gate import WRITE_TOOLS as _WRITE_TOOLS
from hookprobe.gate import append_audit as _append_audit
from hookprobe.gate import tool_detail as _tool_detail
from hookprobe.gate import trips as _guard_trips
from hookprobe.guard import bash_deny_reason
from hookprobe.hygiene import post_tool_hook
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
    # How full the model's context was when this turn ended, and whether the
    # runtime folded any of it away while the turn ran. A production patrol died
    # on "the model has reached its context window limit" and nothing anywhere
    # had said it was close — the number existed and nobody asked for it. Absent
    # when the runtime does not answer, which is the honest shape for a fact we
    # can only be told.
    context: dict[str, Any] | None = None
    compactions: tuple[dict[str, Any], ...] = ()
    # How many times the guard refused this turn. The third outcome the gate did
    # not have: refusing and continuing was the only thing it could do, so a run
    # that argued with the posture twenty times left the same trace as one
    # refused once and rephrased — and those are different runs. Counted from
    # the audit, because the gate is stateless on the spawned runtimes.
    #
    # Recorded, not acted on. Failing a turn on a threshold would change
    # behaviour on upgrade for every deployment, and a legitimate narrowing of
    # a query is refused a few times on the way; the number has to be readable
    # before anybody can pick a limit honestly. See gate.trips.
    guard_trips: int = 0


def _bash_guard_hook(
    mode: str,
    record: Callable[[dict[str, Any]], None] | None = None,
    workdir: Path | None = None,
    home: Path | None = None,
) -> Callable[..., Any]:
    """PreToolUse: refuse a command this runner's posture does not allow.

    Built per instance rather than read from a global, like the MCP guard beside
    it: which posture a runner takes is a property of that runner, and two of
    them share this process in the tests.

    `workdir` brings a second question with it: does this command write a file
    that steers the next run. The input guard cannot answer it, because it reads
    the arguments of Write and Edit and a shell redirect has none — which is how
    `printf 'x' >> CLAUDE.md` got past both guards, on every runtime and under
    both postures, until somebody measured it.
    """

    async def hook(input_data: dict[str, Any], tool_use_id: str | None, context: Any) -> dict[str, Any]:
        command = str((input_data.get("tool_input") or {}).get("command") or "")
        reason = bash_deny_reason(command, mode)
        guard = "bash"
        if reason is None and workdir is not None:
            hit = shell_write_target(command, inputs.protected_paths(workdir, home), workdir)
            if hit is not None:
                guard = "input"
                reason = (
                    f"input guard: this command writes {hit}, which steers the next run. "
                    "A shell redirect is not a way around the guard on the edit tools."
                )
        if reason is None:
            return {}
        logger.warning("bash command denied (%s): %s", mode, command)
        if record is not None:
            record(
                {
                    "tool": "Bash",
                    "detail": command[:300],
                    "denied": True,
                    "guard": guard,
                    "mode": mode,
                    "reason": reason,
                }
            )
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }

    return hook


def _mcp_guard_hook(
    allowed: frozenset[str], record: Callable[[dict[str, Any]], None] | None = None
) -> Callable[..., Any]:
    """PreToolUse: refuse an MCP tool the operator did not name.

    A hook rather than `allowed_tools` alone because the engine runs with
    permission_mode="bypassPermissions", where an allowlist is a preference and
    a hook is a decision — the same reason the bash guard is a hook.
    """

    async def hook(input_data: dict[str, Any], tool_use_id: str | None, context: Any) -> dict[str, Any]:
        reason = mcp_deny_reason(str(input_data.get("tool_name") or ""), allowed)
        if reason is None:
            return {}
        logger.warning("mcp tool denied: %s", input_data.get("tool_name"))
        if record is not None:
            record(
                {
                    "tool": str(input_data.get("tool_name") or ""),
                    "detail": "",
                    "denied": True,
                    "guard": "mcp",
                    "reason": reason,
                }
            )
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }

    return hook


def _input_guard_hook(
    workdir: Path, home: Path | None, record: Callable[[dict[str, Any]], None] | None = None
) -> Callable[..., Any]:
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
                if record is not None:
                    record(
                        {
                            "tool": str(input_data.get("tool_name") or ""),
                            "detail": str(data.get(key) or "")[:300],
                            "denied": True,
                            "guard": "input",
                            "reason": reason,
                        }
                    )
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": reason,
                    }
                }
        return {}

    return hook


def _compaction_hook(record: Callable[[dict[str, Any]], None]) -> Callable[..., Any]:
    """PreCompact: the runtime is about to fold this session's context away.

    Nothing is refused here — it is not a gate. It is the only way to know a
    compaction happened at all, and what it drops it drops: a report with a gap
    in the middle of a long investigation has no other explanation available
    afterwards.
    """

    async def hook(input_data: dict[str, Any], tool_use_id: str | None, context: Any) -> dict[str, Any]:
        record(
            {
                "type": "compacted",
                "trigger": str(input_data.get("trigger") or "")[:40],
                "instructions": str(input_data.get("custom_instructions") or "")[:200],
            }
        )
        return {}

    return hook


# Short on purpose: a control request the runtime answers at all answers fast,
# and one it does not answer is being waited on for nothing.
_CONTEXT_USAGE_TIMEOUT = 3.0


def _context_facts(usage: Any) -> dict[str, Any] | None:
    """The few numbers worth keeping from a context-usage answer.

    Not the whole object: `categories` is a per-kind breakdown that changes
    shape with the runtime, and a record is worth more when it holds the same
    keys next year.
    """
    total = getattr(usage, "totalTokens", None)
    limit = getattr(usage, "maxTokens", None)
    if total is None and limit is None:
        return None
    facts: dict[str, Any] = {"tokens": total, "limit": limit}
    pct = getattr(usage, "percentage", None)
    if pct is not None:
        facts["percent"] = round(float(pct), 1)
    auto = getattr(usage, "isAutoCompactEnabled", None)
    if auto is not None:
        facts["auto_compact"] = bool(auto)
    return facts


def _hook_list(*fns: Callable[..., Any]) -> list[Any]:
    """The SDK types hook inputs as a TypedDict union; ours are plain dicts on
    purpose — the tests never import the SDK, so its types cannot appear in
    signatures. One list[Any] at the exact boundary, instead of ignores at
    every registration site."""
    return list(fns)


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


def _otel_value(value: str) -> str:
    """A W3C-baggage-safe attribute value: no separators, no whitespace."""
    return "".join("_" if ch in ",;= \t\n" else ch for ch in value)[:200]


def _resource_attributes(inherited: str, session_key: str) -> str:
    """The container's static attributes plus this run's pipe key.

    `probe:watch:111` → `hookstack.session_key=probe:watch:111,hookstack.event_id=111`;
    a patrol key such as `patrol:self-review:2026-09-04` carries no event id and
    gets only the first. Anything the operator already set on the container —
    service.namespace, service.name, hookstack.node — stays in front.
    """
    parts = [
        p
        for p in (inherited or "").split(",")
        if p.strip() and not p.startswith("hookstack.session_key=") and not p.startswith("hookstack.event_id=")
    ]
    parts.append(f"hookstack.session_key={_otel_value(session_key)}")
    tail = session_key.rsplit(":", 1)[-1]
    if tail.isdigit():
        parts.append(f"hookstack.event_id={tail}")
    return ",".join(parts)


def _audit_recorder(audit_dir: Path, session_key: str) -> Callable[[dict[str, Any]], None]:
    """What the guards write with when they refuse something.

    The refusals were the strongest evidence this runner has that the agent did
    not do what it was told — "it tried `kubectl delete`, the guard said no" —
    and they went to the process log only, which nobody reads and retention
    does not keep. An audit that lists every tool call but none of the refused
    ones is a record of what happened with the interesting half missing. Same
    file, same shape, one extra field: `denied`.
    """

    def record(extra: dict[str, Any]) -> None:
        try:
            import time

            _append_audit(audit_dir, {"ts": round(time.time(), 3), "session": session_key, **extra})
        except Exception:  # noqa: BLE001 — a refusal must still be a refusal if the recorder fails
            logger.debug("audit write failed", exc_info=True)

    return record


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
            _append_audit(audit_dir, line)
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


def _unrecorded_skills(root: Path, limit: int = 40) -> list[str]:
    """Project-layer skills with no provenance record.

    Every write path the service owns — distill, PUT, restore — leaves an
    origin.json beside the manifest, and on 2026-08-24 all fifteen live
    runbooks had one. So a SKILL.md without one arrived some other way: a file
    dropped onto the volume, a third-party skill copied in, a write that went
    around the service. A skill is standing instruction to an agent that reads
    attacker-influenced text and holds tools — supply-chain shaped, so at
    minimum the inventory says which entries nothing vouches for.
    """
    try:
        names = sorted(p.parent.name for p in root.glob("*/SKILL.md") if not (p.parent / "origin.json").is_file())
    except OSError:
        return []
    return names[:limit]


def _agent_names(root: Path, limit: int = 40) -> list[str]:
    try:
        names = sorted(p.stem for p in root.glob("*.md"))
    except OSError:
        return []
    return names[:limit]


# Subtypes that mean the run was CUT OFF, not that the engine reported a fault.
# On these the final text is the model's last sentence mid-thought, so the
# prefer-the-text rule below does not apply — it put
# `reason=That prior analysis is rich. Now I need to place the current alert…`
# on the live board, which reads as neither an error nor an answer. What an
# operator can act on here is the limit that stopped it.
_CUTOFF_SUBTYPES = {
    "error_max_turns": "stopped at the turn limit before reaching a conclusion",
    "error_max_tokens": "stopped at the output limit before reaching a conclusion",
}


# Failures that are about the moment rather than the request. Matched on the
# text the engine reported, because that is what this service is given — the
# SDK has no error taxonomy to ask. The list is deliberately short and the
# default is "permanent": trying a permanent failure again buys nothing and
# spends a turn, while missing a transient one costs only what it costs today,
# which is a failure an operator can retry by hand.
_TRANSIENT_MARKERS = (
    "429",
    "500",
    "502",
    "503",
    "504",
    "520",
    "521",
    "522",
    "524",
    "408",
    "overloaded",
    "rate limit",
    "connection reset",
    "connection error",
    "temporarily unavailable",
    "service unavailable",
    "internal server error",
)
# Deliberately NOT here: the words "timeout" and "timed out" on their own. A
# wall-clock timeout is this service's own limit, not a provider blip — the
# investigation wanted more time than it was given, and the second attempt will
# want it too. Gateway timeouts, which ARE momentary, arrive with a status code
# (408, 504, 524) and are caught by those.
#
# Checked first: these read as transient by the markers above and are not.
# "The model has reached its context window limit" carries "limit"; a 402 is a
# balance, not a blip; an auth failure will fail identically forever. Retrying
# any of them is a second bill for the same answer.
_PERMANENT_MARKERS = (
    "context window",
    "insufficient balance",
    "401",
    "403",
    "invalid api key",
    "authentication",
    "not found",
    "invalid_request",
    "messageparseerror",
)


def priced(total: Any, tokens: Any) -> float | None:
    """A turn's cost, or `None` — and a zero with tokens behind it is `None`.

    Obligation five of the runtime contract, as ONE function rather than one per
    adapter, for the reason the gate is one module: a rule about money written
    twice means `cost_usd` says slightly different things depending which engine
    a node runs, and nothing in either suite notices the day they diverge.

    pi is where this was measured. It prices a turn from its own model
    catalogue, this deployment's model is not in that catalogue, and the first
    real turn came back with `totalTokens: 6397` and `cost.total: 0`. A budget
    breaker fed that watches an unattended node spend all week and sees nothing
    wrong — worse than a runtime that admits it cannot count, because nothing
    ever looks wrong. So tokens spent with a price of zero is UNPRICED, and only
    a zero with nothing behind it may mean free.

    The Claude adapter passed `total_cost_usd` through raw until 2026-09-09 and
    had never been observed reporting a zero — 89 runs on the work deployment,
    all priced. It runs a gateway model the CLI does not price, though, which is
    exactly pi's condition, so it asks the same question now instead of waiting
    to find out.
    """
    if not isinstance(total, int | float) or isinstance(total, bool):
        return None
    if total == 0 and isinstance(tokens, int | float) and tokens > 0:
        return None
    return float(total)


# Per-MILLION tokens, because that is how a provider states a rate and how the
# operator stated theirs. hookjudge's two constants are per-1K for historical
# reasons; converting at the edge beats carrying a unit nobody quotes.
_PER_MILLION = 1_000_000


def price_tokens(usage: Any, rates: tuple[float, float, float, float]) -> float | None:
    """What this turn cost, from tokens we measured and rates the operator gave.

    The alternative it replaces is worse than it looks. `cost_usd` came from
    whatever the runtime felt like reporting: the Claude CLI prices from its own
    table for a model it is not the one billing, codex reports tokens and never
    money at all, and pi priced from a catalogue this deployment's model is not
    in. So the number was either an estimate for the wrong model or absent —
    and absent is what made a budget ceiling unable to bind on two of three
    runtimes.

    Tokens, by contrast, every runtime reports and none of them has to guess.
    Multiplying them by the operator's own stated rate for their own gateway is
    the first cost figure in this stack that is arithmetic over two measured
    things rather than a table somebody else maintains.

    It is still PRICED and not BILLED, and the weekly page still says so — a
    rate can be out of date and a gateway can bill for something these four
    numbers do not name. But it is priced from the right table, which the
    previous number was not.

    `None` when no rates are configured, which is every deployment until one
    sets them: silence is how this stays a change nobody gets by upgrading.
    """
    fresh_rate, cache_read_rate, cache_write_rate, out_rate = rates
    if not any(rate > 0 for rate in rates):
        return None
    if not isinstance(usage, dict):
        return None
    fresh = float(usage.get("input_tokens") or 0)
    cached = float(usage.get("cache_read_input_tokens") or 0)
    written = float(usage.get("cache_creation_input_tokens") or 0)
    out = float(usage.get("output_tokens") or 0)
    if not (fresh or cached or written or out):
        return None
    total = (fresh * fresh_rate + cached * cache_read_rate + written * cache_write_rate + out * out_rate) / _PER_MILLION
    # Six places: a turn costing a fraction of a cent is normal here, and
    # rounding it to four would make a hundred of them disappear.
    return round(total, 6)


def transient(error: str) -> bool:
    """Whether this failure is worth one more attempt, right now.

    Production evidence for the shape of this: two real alert investigations on
    2026-09-04 died on `API Error: 524` — a gateway timeout — and sat in the
    board's "needs a human" column for four days until somebody read it. By
    then re-investigating was spending money on a question whose answer had
    stopped mattering. The moment to try again is the moment, not four days on.
    """
    lowered = " ".join(str(error or "").split()).lower()
    if not lowered or any(marker in lowered for marker in _PERMANENT_MARKERS):
        return False
    return any(marker in lowered for marker in _TRANSIENT_MARKERS)


# How an engine's own error line opens. Checked against the head of the text,
# because a provider error is a line and an answer is prose or JSON.
_ERROR_LINE_MARKERS = ("api error", "error:", "http error", "connection", "parseerror", "exception")


def _looks_like_an_error_line(detail: str) -> bool:
    head = detail[:60].lower()
    return any(marker in head for marker in _ERROR_LINE_MARKERS)


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
        subtype = str(getattr(result, "subtype", "") or "")
        cutoff = _CUTOFF_SUBTYPES.get(subtype)
        if cutoff:
            return f"{cutoff} ({subtype})"
        detail = " ".join(text.split())
        # Quote the text only when the text IS the error. A provider's line —
        # "API Error: 402 Insufficient Balance", "API Error: 524 {…}" — is the
        # most useful reason there is, and `transient()` reads it to decide
        # whether to try again, so it must survive verbatim.
        #
        # When the text is the run's own ANSWER, quoting it produced a board
        # entry that read like a finished report wearing the word "failed": a
        # patrol on 2026-09-03 was flagged with an unmapped subtype and its
        # reason became "One inference needs checking — the skill cases show…",
        # a sentence with no failure in it. Name what happened instead, and let
        # `failure_report` keep the answer.
        if detail and _looks_like_an_error_line(detail):
            return detail[:200]
        if detail:
            return f"engine reported {subtype or 'error'} after producing an answer"
        return f"engine reported {subtype or 'error'}"
    if not text:
        return "engine returned an empty result"
    return None


class ClaudeAgentEngine:
    """The one runtime adapter: the Claude Code SDK, driven per turn.

    Sessions and resume are the whole point now — a follow-up continues what the
    first pass gathered, and a restart continues what a killed process left (see
    `RunService.recover_orphans`). The obligations this has to meet, including
    the two that are invisible in the Protocol's signatures, are written on
    `service.Engine`.
    """

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
        # Whether this runtime answers "how full is the context". Latched off on
        # the first failure — see _context_usage for what asking every turn cost.
        self._context_usage_works = True

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

    # Secrets the SERVICE holds but the agent's shell has no business reading.
    # The SDK inherits os.environ into the CLI subprocess, so the way to REMOVE a
    # variable is to override it to "" in options.env — which is what this does.
    # Two groups: the family's own HMAC keys (with them the agent could forge
    # signed reports, rulings and escalations that the service exists to sign for
    # it), and the platform/sibling secrets that share the deployment's .env
    # (Lark app, the shadow ingest secret that can forge judgements, the admin
    # token). The service already read what it needs at startup.
    #
    # HOOKPROBE_TOKEN is deliberately NOT blanked: the run-rulings patrol has the
    # agent POST to this service's OWN API with it. That the agent holds a bearer
    # to its own write surface is a real residual; narrowing it to a lesser,
    # read-or-rulings-only credential is a separate change, recorded in
    # .agents/notes/proposed/2026-09-02-the-agent-shares-the-services-secrets.md.
    # ANTHROPIC_*/model keys are left intact — the model call needs them.
    # One list, in gate.py, shared with every adapter that spawns its gate. It
    # was a private tuple here until 2026-09-09, and being private is how two
    # later adapters came to inherit every secret it names.
    _SECRETS_WITHHELD_FROM_AGENT = SECRETS_WITHHELD_FROM_AGENT

    def _subprocess_env(self, session_key: str = "") -> dict[str, str]:
        """The env overrides for the agent's CLI subprocess: per-command
        deadlines, plus the service's own secrets blanked so a Bash step (or an
        injected instruction that reaches one) cannot read them out of the
        environment. See _SECRETS_WITHHELD_FROM_AGENT for what and why.

        And the run's identity, stamped onto its telemetry. The CLI emits one
        OpenTelemetry event per model call, keyed by its own session UUID; the
        pipe keys everything by event id; joining the two took a person and two
        lookups. So every event this run emits carries the pipe's key as a
        resource attribute — `hookstack.session_key`, and `hookstack.event_id`
        when the key embeds one — and a Grafana filter on one event id returns
        every model call of that operation across every node that worked on it.
        The container's own attributes (deployment, service, node) are kept in
        front; the SDK REPLACES the inherited variable with this one, so the
        static half has to be carried through here or it would be lost.

        And where that telemetry goes: with the receiver on, the CLI posts to
        this service (telemetry.py), which keeps the run's timing record and
        forwards to the operator's collector if one is named — so the run's
        waterfall is on its own page whether or not anything else is deployed.
        """
        env = self._engine_env()
        env.update(telemetry.subprocess_env(self._settings))
        for name in self._SECRETS_WITHHELD_FROM_AGENT:
            env[name] = ""
        if session_key:
            env["OTEL_RESOURCE_ATTRIBUTES"] = _resource_attributes(
                os.environ.get("OTEL_RESOURCE_ATTRIBUTES", ""), session_key
            )
        # And the same identity in the one format a tool INSIDE a turn
        # recognises. gate.trace_environment is the shared half: the two
        # spawning adapters get it from gate.environment, this one from here,
        # and all three derive it the same way or the derivation is worthless.
        env.update(trace_environment(session_key))
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
            # Always present, usually []: an empty list is the healthy claim,
            # and an absent key is indistinguishable from "nobody checked" —
            # the `review: []` false all-clear, not repeated.
            skills["unrecorded"] = _unrecorded_skills(self._workdir / ".claude" / "skills")
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
            # The posture this run held: what its shell could not do and which MCP
            # tools it could call. Recorded on the run so an audit can say "this
            # investigation ran read-only" from the record, not from today's config.
            "posture": {"bash_guard": self._settings.bash_guard, "mcp_tools": sorted(self._settings.mcp_tools)},
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
        # Compactions this turn, kept for the record and echoed into the feed so
        # a person watching sees the moment the context was folded.
        compactions: list[dict[str, Any]] = []

        def _note_compaction(event: dict[str, Any]) -> None:
            compactions.append(event)
            emit(event)

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
        recorder = _audit_recorder(self._workdir / "audit", session_key)
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
            # The declared MCP tools ride along so the CLI exposes them at all.
            # This is visibility, not enforcement — with bypassPermissions an
            # allowlist is a preference. _mcp_guard_hook is the gate.
            allowed_tools=[*_ALLOWED_TOOLS, *sorted(self._settings.mcp_tools)],
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
                    HookMatcher(
                        matcher="Bash",
                        hooks=_hook_list(
                            _bash_guard_hook(self._settings.bash_guard, recorder, self._workdir, self._home)
                        ),
                    ),
                    # Matched on every tool, filtered by name inside: the guard
                    # must not depend on how the SDK interprets a matcher
                    # pattern for the one thing it exists to stop.
                    HookMatcher(matcher=None, hooks=_hook_list(_input_guard_hook(self._workdir, self._home, recorder))),
                    # matcher=None for the same reason as the line above: the
                    # one guard standing between a colleague's chat message and
                    # a tool that can act as the operator must not depend on
                    # how the SDK interprets an `mcp__*` matcher pattern.
                    HookMatcher(matcher=None, hooks=_hook_list(_mcp_guard_hook(self._settings.mcp_tools, recorder))),
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
                # Not a gate: nothing is refused here, and it is the only way to
                # know the runtime folded the context away while a turn ran.
                "PreCompact": [HookMatcher(matcher=None, hooks=_hook_list(_compaction_hook(_note_compaction)))],
            },
            # Per-command deadlines, the service secrets blanked, and this run's
            # pipe key on its telemetry; see _subprocess_env.
            env=self._subprocess_env(session_key),
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
        session_seen = ""
        result: Any = None
        # Wall clock, because the audit lines are stamped with it.
        wall_started = time.time()
        # None on every path that never reaches the probe below.
        context: dict[str, Any] | None = None
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
                # The id that makes this turn resumable, published the moment
                # the runtime first mentions it rather than at the end with the
                # result. A process killed mid-turn used to leave a run with no
                # session id and therefore nothing to continue — the whole
                # investigation thrown away because the handle to it arrived
                # one message too late. `getattr`, because a runtime that does
                # not carry one simply never emits this and the caller falls
                # back to failing the run, which is the old behaviour.
                if not session_seen:
                    found = str(getattr(msg, "session_id", "") or "")
                    if found:
                        session_seen = found
                        emit({"type": "session", "id": found})
                if isinstance(msg, StreamEvent):
                    # Transient by design: deltas are for a human watching right
                    # now. The finished blocks below are what gets recorded — and
                    # what gets COUNTED, which is why the counter sits after this
                    # branch rather than at the top of the loop.
                    #
                    # `include_partial_messages=True` (above) makes the SDK emit
                    # one of these per token-level delta, so counting them turned
                    # `messageCount` on /final into a token counter: a production
                    # investigation reported 32,294 for what the same record calls
                    # one turn, and the service logged it as `turns=32294`.
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
                message_count += 1
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
            # BEFORE the finally, because the finally disconnects. This call
            # used to sit AFTER it, so the probe always ran against a closed
            # client: it could not succeed on any runtime, the latch then
            # recorded "this runtime does not report context usage", and every
            # run's `context` was null for a reason that had nothing to do with
            # the runtime. Found by tests/test_scripted_turns.py, which counts
            # and ORDERS the calls the loop makes on its client.
            #
            # One extra round trip to the CLI, no model call, and never fatal:
            # a failure to ANSWER how full the context is must not fail a turn
            # that already produced a report.
            context = await self._context_usage(client)
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
            # Obligation five, through the shared rule: the CLI prices from its
            # own table for a model it is not the one billing, so a zero here is
            # "could not price this" far more likely than "this was free".
            cost_usd=priced(
                getattr(result, "total_cost_usd", None),
                sum(v for k, v in (usage or {}).items() if isinstance(v, int | float) and "token" in k)
                if isinstance(usage, dict)
                else None,
            ),
            error=error,
            session_id=getattr(result, "session_id", None),
            usage=dict(usage) if isinstance(usage, dict) else None,
            model_usage=dict(model_usage) if isinstance(model_usage, dict) else None,
            duration_ms=getattr(result, "duration_ms", None),
            input_changes=input_changes,
            context=context,
            compactions=tuple(compactions),
            guard_trips=_guard_trips(self._workdir / "audit", session_key, since=wall_started),
        )

    async def _context_usage(self, client: Any) -> dict[str, Any] | None:
        """How full the context is, asked at most once per process if it fails.

        This is an extra request to the CLI on the hot path of every turn, so it
        is bounded twice. A timeout, because an observation that can hang is not
        an observation — it is an outage waiting for the runtime to stop
        answering. And a latch, because the first version of this cost **ten
        seconds on every turn**: CLI 2.1.259 does not answer the request at all,
        so a trivial turn that should take two and a half seconds took twelve
        and a half, and the number it was buying was never going to arrive. Ask
        once, learn, stop asking.

        The latch is per PROCESS, not per run: a deployment that upgrades its
        CLI gets the number back on the next restart, which is when its runtime
        changed anyway.
        """
        if not self._context_usage_works:
            return None
        try:
            return _context_facts(await asyncio.wait_for(client.get_context_usage(), timeout=_CONTEXT_USAGE_TIMEOUT))
        except Exception:  # noqa: BLE001 — an observation is not worth a failed run, or a slow one
            self._context_usage_works = False
            logger.info(
                "this runtime does not report context usage; not asking again in this process "
                "(the run record's `context` will stay null)"
            )
            return None

    def _input_changes(self, before: dict[str, str]) -> tuple[str, ...]:
        """What this run did to its own input surface — empty when it behaved."""
        found = tuple(inputs.changes(before, inputs.fingerprint(self._workdir, self._home)))
        if found:
            logger.warning("run rewrote its own inputs: %s", "; ".join(found))
        return found
