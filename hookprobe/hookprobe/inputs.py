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
from collections.abc import Iterable
from pathlib import Path

# Everything the model is handed as standing instruction — plus the audit log,
# which is here for a different reason: a record its own subject can rewrite is
# not a record.
_PROTECTED = (".claude", "CLAUDE.md", "system-prompt.md", "audit")

# Hashed before and after a run. `audit/` is deliberately absent: the flight
# recorder appends a line per tool call, so it changes during every healthy run
# and would report tampering on all of them.
_FINGERPRINTED = (".claude", "CLAUDE.md", "system-prompt.md")

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


def protected_paths(workdir: Path, home: Path | None = None) -> tuple[Path, ...]:
    """Paths this runner may read but never write."""
    paths = [workdir / name for name in _PROTECTED]
    if home is not None:
        # The host skills library, when HOOKPROBE_SETTING_SOURCES loads "user".
        paths.append(home / ".claude")
    return tuple(_resolve(path) for path in paths)


def write_deny_reason(raw_path: str, *, workdir: Path, home: Path | None = None) -> str | None:
    """Why this write is refused, or None when it may proceed."""
    if not raw_path:
        return None
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        # Tool paths are relative to the agent's cwd, which is the workdir.
        candidate = workdir / candidate
    candidate = _resolve(candidate)
    for root in protected_paths(workdir, home):
        if candidate == root or root in candidate.parents:
            return (
                f"input guard: {root} steers the next run, so this runner may not write it. "
                "Turning a finding into a runbook is an operator action — distill the run, "
                "then have an operator install it — never a run editing its own instructions."
            )
    return None


def fingerprint(workdir: Path, home: Path | None = None) -> dict[str, str]:
    """One digest per input file, for a before/after comparison across a run."""
    roots = [workdir / name for name in _FINGERPRINTED]
    if home is not None:
        # Only the skills subtree: the rest of $HOME/.claude is the CLI's own
        # transcript state, which changes on every run by design.
        roots.append(home / ".claude" / "skills")
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
