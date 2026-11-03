"""The agent's own input surface: the files that steer the *next* run.

A run is steered by files on a writable volume — the environment memory, the
appended methodology, the runbooks earlier runs distilled, the subagent roles.
The agent holds Write and Edit, runs under `bypassPermissions`, and its working
directory is the directory those files live in. Nothing stopped a run from
editing them, and the next run would load the edit as instruction.

That is what turns a single prompt injection into a durable one. The engine
fences untrusted alert text, so an injected line steers at most the run that
read it; an injected line that reaches `.claude/skills/` steers every run after
it, and reads back as the operator's own runbook. Distilling a finding into a
runbook is deliberately an operator action (`POST /v1/runs/{key}/distill`, then
`PUT /v1/skills/{name}`) — it was never meant to be something a run could do to
itself.

Two defences, kept separate because they fail differently:

* `write_deny_reason` refuses the Write/Edit and says why. It is exact, and it
  is what an injected instruction actually reaches for first.
* `fingerprint` is taken before and after every run and compared. It does not
  care *how* a file changed, so it still catches what a shell one-liner does
  behind the tool guard's back — the bash guard's own docstring declines to
  claim a regex is a boundary, and this is the reason it can afford to.

Neither touches the operator. Both live on the agent's tool path, while
`PUT /v1/memory`, `PUT /v1/skills/{name}` and friends run in the service and
are untouched by either. That distinction is the whole reason this is code and
not a read-only mount: agent and service share a UID and a process tree, so a
mount cannot tell them apart, and mounting these paths read-only would take the
operator's four write endpoints down with the attack.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

# Everything the model is handed as standing instruction — plus the audit log,
# which is here for a different reason: a record its own subject can rewrite is
# not a record.
# memory-suggestions.jsonl: the agent PROPOSES memory through its report;
# writing the queue directly would let it forge attribution or flush other
# runs' suggestions. (A bash write around this guard can at worst stuff
# the queue with rows an operator dismisses — never write memory itself.)
# remediation/: the agent proposes through its report; a direct write could
# only forge a proposal's provenance. (Even forged, a proposal is inert —
# approval and the allowlist stand between any proposal and execution.)
# actions/: the card door's idempotency ledger. A pre-written claim cannot cause
# an action, but it can swallow one — the genuine press would read the forged
# row, decline to act because the key is taken, and report back whatever that
# row claimed had happened. A record its own subject can write is not a record.
# results/: the case files every prompt tells the run to open FIRST as earlier
# evidence. They are written by the service at the end of a run, never by the
# run; leaving them writable let one injected run forge or delete the evidence
# the next one treats as prior knowledge. Scratch space is elsewhere.
_PROTECTED = (
    ".claude",
    "CLAUDE.md",
    # The same file under the name every other runtime reads it by. Codex and
    # pi both load AGENTS.md as the standing instructions for the next run, so
    # leaving it out meant this guard protected "the files that steer the next
    # run" only on the runtime it was written for.
    "AGENTS.md",
    "system-prompt.md",
    "audit",
    "memory-suggestions.jsonl",
    "remediation",
    "actions",
    # telemetry/: the run's own timing record — model calls, tool durations,
    # cost — written by the service from what the CLI reported. A run that
    # could edit it could bill itself less; same rule as audit/.
    "telemetry",
    "results",
)

# Hashed before and after a run. `audit/` is deliberately absent: the flight
# recorder appends a line per tool call, so it changes during every healthy run
# and would report tampering on all of them.
_FINGERPRINTED = (".claude", "CLAUDE.md", "system-prompt.md")

# The files an operator points this service at by path. They steer every run as
# surely as the names above do — the MCP config is started as processes at the
# top of every run — but they live wherever the operator put them, so they are
# protected by the configured path rather than by a name in the workdir. Found
# 2026-11-02: a deployment kept its MCP config on the run's volume, one run wrote
# a command into it, and every later run started that command with no tool call
# for the guards or the audit to see. The service reads these and never writes
# them, so the boundary that holds is a read-only mount; see `writable`.
_OPERATOR_FILES = (
    "mcp_config",
    "agents_config",
    "remediation_allowlist",
    "remediation_high_risk_allowlist",
    "blast_radius",
    "decline_patterns",
    "codex_config",
    "pi_config",
)

# Bounds, so that a run cannot make its own verification expensive by dropping
# a very large or very deep tree into the skills directory.
_MAX_FILES = 400
_MAX_BYTES = 1 << 20


def _resolve(path: Path) -> Path:
    """Absolute and symlink-free, so a link cannot walk around the guard."""
    try:
        return path.resolve()
    except OSError:
        return path


def operator_files(settings: Any) -> tuple[Path, ...]:
    """The configured files only an operator writes: the service reads them and
    never writes them, so nothing running here should be able to either."""
    found = (getattr(settings, name, None) for name in _OPERATOR_FILES)
    return tuple(Path(path) for path in found if path)


def configured(settings: Any) -> tuple[Path, ...]:
    """Every file a run is steered by through configuration: the operator's
    files, and the appended methodology when its path is configured. The
    service writes that one for the operator (`PUT /v1/system-prompt`), so it is
    guarded here and never expected to be read-only."""
    append = getattr(settings, "system_prompt_append", None)
    return (*operator_files(settings), *((Path(append),) if append else ()))


def protected_paths(workdir: Path, home: Path | None = None, configured: Iterable[Path] = ()) -> tuple[Path, ...]:
    """Paths this runner may read but never write."""
    paths = [workdir / name for name in _PROTECTED]
    if home is not None:
        # The host skills library, when HOOKPROBE_SETTING_SOURCES loads "user".
        paths.append(home / ".claude")
    paths.extend(configured)
    return tuple(_resolve(path) for path in paths)


def writable(paths: Iterable[Path]) -> list[str]:
    """The configured files this process's own user could write or replace.

    The guard on the tool path catches the obvious writes and is not a
    boundary; the agent shares this uid. What holds is a file this uid cannot
    write at all: a read-only mount, or a file and directory another user owns.
    A file in a directory this uid can write is replaceable even when the file
    itself is read-only, and a configured path that does not exist yet in such
    a directory can be created, so both count.
    """
    found: list[str] = []
    for path in paths:
        target = _resolve(path)
        if (target.exists() and os.access(target, os.W_OK)) or os.access(target.parent, os.W_OK):
            found.append(str(path))
    return found


def write_deny_reason(
    raw_path: str, *, workdir: Path, home: Path | None = None, configured: Iterable[Path] = ()
) -> str | None:
    """Why this write is refused, or None when it may proceed."""
    if not raw_path:
        return None
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        # Tool paths are relative to the agent's cwd, which is the workdir.
        candidate = workdir / candidate
    candidate = _resolve(candidate)
    operator = {_resolve(path) for path in configured}
    for root in protected_paths(workdir, home, configured):
        if candidate == root or root in candidate.parents:
            if root in operator:
                return (
                    f"input guard: {root} is configuration this node reads fresh at every run, "
                    "so no run may write it. An operator changes it, outside any run."
                )
            return (
                f"input guard: {root} steers the next run, so this runner may not write it. "
                "Turning a finding into a runbook is an operator action — distill the run, "
                "then have an operator install it — never a run editing its own instructions."
            )
    return None


def fingerprint(workdir: Path, home: Path | None = None, configured: Iterable[Path] = ()) -> dict[str, str]:
    """One digest per input file, for a before/after comparison across a run."""
    roots = [workdir / name for name in _FINGERPRINTED]
    if home is not None:
        # Only the skills subtree: the rest of $HOME/.claude is the CLI's own
        # transcript state, which changes on every run by design.
        roots.append(home / ".claude" / "skills")
    # The configured files too: whatever way a run found to rewrite one, the
    # comparison still sees that it changed.
    roots.extend(configured)
    facts: dict[str, str] = {}
    for root in roots:
        for path in _files(root):
            if len(facts) >= _MAX_FILES:
                return facts
            facts[str(path)] = _digest(path)
    return facts


def changes(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """What a run did to its own inputs, in words, or an empty list."""
    found: list[str] = []
    for path in sorted(set(before) | set(after)):
        was, now = before.get(path), after.get(path)
        if was == now:
            continue
        found.append(f"{'created' if was is None else 'deleted' if now is None else 'modified'} {path}")
    return found


def _files(root: Path) -> Iterable[Path]:
    try:
        if root.is_file():
            return [root]
        if not root.is_dir():
            return []
        return sorted(path for path in root.rglob("*") if path.is_file())
    except OSError:
        return []


def _digest(path: Path) -> str:
    """Size and a digest of the first megabyte — bounded on purpose; see above."""
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            head = handle.read(_MAX_BYTES)
    except OSError:
        return "unreadable"
    return f"{size}:{hashlib.sha256(head).hexdigest()[:12]}"
