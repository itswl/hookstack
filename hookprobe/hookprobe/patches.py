"""What a work run changed, as a patch a person can review before pushing.

The work runner executes against a remote-less clone, which means its commits
are complete exactly when they are invisible: nothing outside
`work-data/probe-work-code/` knows they exist, and the operator who is supposed
to review them has to know where to look. The handoff's whole promise is that
the review stays human — and a review nobody can find is not a review.

So the brief's report contract ends with a fenced ```patch block carrying the
run's full diff, and this module is the lift: the same shape as
```remediation and ```blocked, for the same reason — prose at the bottom of a
report is read by a person once and by nothing ever again.

The service writes the patch to `patches/<session>.patch` and records only the
bookkeeping on the run (`meta["patch"]`: file, lines, adds, dels). The diff is
an artifact, not metadata: it can be tens of KB, and meta travels with the run
into cards, boards and telemetry.

What this does NOT do: validate that the patch matches the commits on disk.
The agent writes the block, so a patch that lies about the tree is possible
the same way a mislabelled `risk` is possible — the floor is "the obvious
form, honestly reported". The review it exists to serve is the check; if the
diff and the clone ever disagree, that is a finding about the runner, and the
commit history in the clone is the ground truth to diff against.
"""

from __future__ import annotations

import re

_BLOCK = re.compile(r"```patch\s*\n(.*?)```", re.DOTALL)
_MAX = 128 * 1024


def extract(text: str) -> str:
    """The run's diff, or "". Validated as a diff, not just fenced text.

    Must start with a `diff --git` line: a fenced block that is not a diff is
    a contract violation worth dropping rather than storing, because the thing
    downstream of it (a person clicking "review the patch") would get prose
    where they expected a diff.
    """
    match = _BLOCK.search(text or "")
    if not match:
        return ""
    body = match.group(1).strip()
    if not body.startswith("diff --git"):
        return ""
    return body[:_MAX]


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
