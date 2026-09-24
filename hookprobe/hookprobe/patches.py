"""What a work run changed, as a patch a person can review before pushing.

The work runner executes against a remote-less clone, which means its commits
are complete exactly when they are invisible: nothing outside
`work-data/probe-work-code/` knows they exist, and the operator who is supposed
to review them has to know where to look. The handoff's whole promise is that
the review stays human — and a review nobody can find is not a review.

So the brief's report contract ends with a fenced ```diff block carrying the
run's full diff, and this module is the lift: the same shape as
```remediation and ```blocked, for the same reason — prose at the bottom of a
report is read by a person once and by nothing ever again.

The service writes the patch to `patches/<session>.patch` and records only the
bookkeeping on the run (`meta["patch"]`: file, lines, adds, dels). The diff is
an artifact, not metadata: it can be tens of KB, and meta travels with the run
into cards, boards and telemetry.

The agent writes the block, so on its own the patch is a CLAIM about the
tree. But the clone it claims to describe is mounted in this same container,
and the brief tells the runner to commit with the plan's session key in the
message and to end with `git diff` over exactly those commits. Both halves are
checkable, so `verify` checks them: the commits carrying the key are found and
their combined diff compared with the block. `matches` on the run's meta is
True, False, or None (no commit carried the key: an unbacked diff). The
review still happens from the report — but now the report says whether the
clone agrees with it, instead of leaving that to whoever remembers to look.
"""

from __future__ import annotations

import os
import re
import subprocess  # nosec B404
from collections.abc import Sequence
from pathlib import Path
from typing import Any

# `diff` first: it is the fence the runner actually writes. The first live run
# (2026-09-22) was told "a fenced patch block", produced a textbook unified diff
# under ```diff — the label every Markdown renderer colours — and the lift,
# matching ```patch only, returned 404 on a report that carried the whole
# change. The contract names the content; the label is whichever of the two
# conventional ones the model reaches for.
# The closing fence must open its own line. A unified diff that ADDS a fenced
# code block — a README documenting an endpoint, which is most of them — carries
# `+```text` inside it, and a non-greedy `(.*?)```` stops dead on those three
# characters. Trial 5 (2026-09-22) returned 44 of its 168 diff lines that way:
# the runner wrote the whole change, the lift truncated it, and a reviewer would
# have reviewed a third of a patch believing it was all of it. Caught only
# because `verify` compared the stored patch against the clone and said they
# DIFFER — the check finding a bug in the code standing next to it.
#
# Column zero exactly, for the close and the open. Leading whitespace is NOT
# allowed, and that is the harder half of the call: a unified diff's CONTEXT
# lines begin with a single space, so a README that already had a fence shows
# up as ` ```text` — indistinguishable from a fence indented inside a list item.
# One of the two has to lose, and the diff-of-a-Markdown-file is the case this
# system actually produces, while the brief asks for the block at the end of the
# report rather than nested in a list.
_BLOCK = re.compile(r"^```(?:diff|patch)[^\n]*\n(.*?)^```", re.DOTALL | re.MULTILINE)
_MAX = 128 * 1024


def extract(text: str) -> str:
    """The run's diff, or "". Validated as a diff, not just fenced text.

    Must start with a `diff --git` line: a fenced block that is not a diff is
    a contract violation worth dropping rather than storing, because the thing
    downstream of it (a person clicking "review the patch") would get prose
    where they expected a diff. The LAST valid block wins: the contract puts the
    patch at the end of the report, and a report may quote an earlier diff
    while explaining what it found.
    """
    for match in reversed(list(_BLOCK.finditer(text or ""))):
        body = match.group(1).strip()
        if body.startswith("diff --git"):
            return body[:_MAX]
    return ""


def counts(patch: str) -> dict[str, int]:
    """The bookkeeping a board wants: adds, dels, files. Zeroes for empty."""
    adds = dels = files = 0
    for line in patch.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            adds += 1
        elif line.startswith("-") and not line.startswith("---"):
            dels += 1
        elif line.startswith("diff --git"):
            files += 1
    return {"adds": adds, "dels": dels, "files": files}


# git's well-known empty tree: what a root commit is diffed against.
_EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


# The clone is writable BY THE AGENT for the whole run, `.git/config` and
# `.gitattributes` included, and several git settings name a program git then
# executes. `git diff` in that repository runs `diff.external` — as this
# service, which holds the signing keys the agent is denied. The verifier that
# exists to check the agent's claim would be the thing running the agent's code.
#
# Each is a FLAG, not a config override, and that distinction cost a debug
# round: `-c diff.external=` does not mean "no external diff", it means "run the
# empty string", and git stops with `external diff died` on the first file. The
# flags are what turn the feature off — `--no-ext-diff` for `diff.external`,
# `--no-textconv` for a driver a `.gitattributes` in the clone selects,
# `--no-pager` for `core.pager`. `core.fsmonitor=false` is the one that has a
# disabling VALUE. The env vars drop the system and global config files, which
# this container has no business reading either way.
#
# Verified rather than reasoned about (2026-09-24): with `diff.external` and a
# textconv driver planted in a clone's own `.git/config`, a plain `git diff`
# runs both, this invocation runs neither, and the diff it returns is
# byte-identical to the same commits' diff in a clean repository.
_GIT_SAFE = ("--no-pager", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null")
_GIT_NO_PROGRAMS = ("--no-ext-diff", "--no-textconv")
_GIT_ENV = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0"}


def _git(repo: Path, *args: str) -> str:
    # Fixed argv, no shell; the only variable parts are a repo path under the
    # code mount and a session key passed as a fixed string, never a pattern.
    done = subprocess.run(  # nosec B603 B607
        ["git", *_GIT_SAFE, "-C", str(repo), args[0], *_GIT_NO_PROGRAMS, *args[1:]],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
        env={**os.environ, **_GIT_ENV},
    )
    return done.stdout


def _normalise(diff: str) -> str:
    # Two `git diff` invocations over the same commits differ only in `index`
    # lines (abbreviation length follows the object count) and trailing
    # whitespace. Everything else is the change, and must be identical.
    kept = [line.rstrip() for line in (diff or "").splitlines() if not line.startswith("index ")]
    return "\n".join(kept).strip()


def verify(patch: str, code_root: Path, keys: Sequence[str | None] | str) -> dict[str, Any]:
    """Is the lifted diff the diff of commits that exist?

    Looks under `code_root` (the clone mount, `/data/code` on the work node)
    for a repository whose history carries one of `keys` in a commit message —
    the brief's own convention — and compares the combined diff of those
    commits with `patch`. Returns `repo`, `commits` (short shas, newest first,
    like every list here) and `matches`: True, False, or None when no commit
    carried any key.

    `keys` is plural because the run and the work are not named the same
    thing. The brief tells the runner to open its commit message with THE
    PLAN's session key; the run doing the committing is `probe:plan-approved:N`.
    Handed only the latter (2026-09-22, trial 4) this searched for a string no
    commit would ever carry and reported `matches: null` over a commit sitting
    in the clone, correctly written — a verifier looking in the right repo for
    the wrong name, which reads exactly like the failure it exists to catch.

    None is the loud case and it stays loud: "no commit found for this run"
    is what that run's page should say, in those words, rather than a diff
    nobody can apply.
    """
    absent: dict[str, Any] = {"repo": None, "commits": [], "matches": None}
    wanted = [str(k).strip() for k in ([keys] if isinstance(keys, str) else keys) if str(k or "").strip()]
    if not wanted or not code_root.is_dir():
        return absent
    for candidate in sorted(code_root.iterdir()):
        if not (candidate / ".git").exists():
            continue
        shas: list[str] = []
        for key in wanted:
            try:
                out = _git(candidate, "log", "--all", "--reverse", "--fixed-strings", f"--grep={key}", "--format=%H")
            except (OSError, subprocess.SubprocessError):
                continue
            shas = out.split()
            if shas:
                break
        if not shas:
            continue
        try:
            theirs = _git(candidate, "diff", f"{shas[0]}^", shas[-1])
        except subprocess.CalledProcessError:
            try:
                theirs = _git(candidate, "diff", _EMPTY_TREE, shas[-1])  # a root commit has no parent
            except (OSError, subprocess.SubprocessError):
                return {"repo": candidate.name, "commits": [s[:7] for s in reversed(shas)], "matches": False}
        except OSError:
            return {"repo": candidate.name, "commits": [s[:7] for s in reversed(shas)], "matches": False}
        return {
            "repo": candidate.name,
            "commits": [s[:7] for s in reversed(shas)],
            "matches": _normalise(theirs) == _normalise(patch),
        }
    return absent
