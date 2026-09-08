#!/usr/bin/env python3
"""One ordering convention across three services, and deviations named on purpose.

Every list a person reads here answers the same question first — what just
happened — so every list is newest first: the run list, skill history, the review
queue, open proposals, waiting memory lines, the judgements a card was made from.
Nine docstrings say so.

One did not. `/v1/audit` served newest LAST, because that is the order a JSONL
file is appended in, and the append order reached the reader, then the page, then
a UI label reading `196 calls (newest last)`. Being written down made it look
decided. It was not decided; it was inherited, and the cost was that the call you
were looking for was at the bottom of 196 rows.

WHAT THIS CHECKS, AND WHAT IT CANNOT

It reads the ordering the code CLAIMS, not the ordering it performs. A docstring
saying "newest first" over a function that reverses twice would pass. That is a
real limit and it is still worth having, because the failure this is built from
was not a wrong sort — it was a correct sort in the wrong direction, described
accurately, and left alone for weeks because nothing objected to the description.

Deciding a direction is cheap. Noticing that three services quietly hold three
opinions is not, and that is the part a person will not do again.

    python3 scripts/assert_ordering.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

SERVICES = ("hookrelay", "hookjudge", "hookprobe")

# The convention. Anything claiming a different order has to be listed below.
WANTED = "newest first"

# Phrasings that mean "not newest first". Matched case-insensitively against
# comments and docstrings, which is where an ordering gets stated in this repo.
DEVIATIONS = (
    re.compile(r"newest\s+last", re.I),
    re.compile(r"oldest\s+first", re.I),
    re.compile(r"chronological(?:ly)?\s+order", re.I),
)

# Deviations that are correct. Keyed by "path:phrase" so moving the line does not
# silently re-approve a different one; the value is why, for whoever reads it next.
SANCTIONED: dict[str, str] = {
    # `run.turns` is a TRANSCRIPT, not a recency list. A conversation is read from
    # the beginning: the first turn is the question, and a follow-up only makes
    # sense after the answer it followed. Reversing it would be reversing a
    # sentence. Found by this check on its first run, which is the argument for
    # having it — the direction was right and nothing said why.
    "hookprobe/hookprobe/runs.py:oldest first": (
        "run.turns is a transcript; a conversation reads forwards, and a follow-up "
        "is meaningless above the answer it followed"
    ),
    # The automation record is the same case as /v1/audit above: a JSONL read in
    # append order because the thing that consumes it — stats() — needs chronology
    # to take the last window and let the last decision on a proposal win. It is
    # not a list a person reads; the page (`/v1/automation`) serves a per-class
    # summary, not these rows. Turning it around would break the window, not a UI.
    "hookprobe/hookprobe/automation.py:oldest first": (
        "the record is a JSONL consumed by stats() in chronological order; the "
        "reader-facing view is review(), which is per-class, not these rows"
    ),
}

# Files that talk about ordering for reasons unrelated to a reader: a sort key in
# a test fixture, a merge that has to be deterministic.
SKIP = ("tests/",)


def offenders() -> list[str]:
    found: list[str] = []
    for service in SERVICES:
        for path in sorted(Path(service, service).rglob("*.py")):
            rel = str(path)
            if any(part in rel for part in SKIP):
                continue
            text = path.read_text(encoding="utf-8")
            for line_no, line in enumerate(text.splitlines(), 1):
                for pattern in DEVIATIONS:
                    match = pattern.search(line)
                    if not match:
                        continue
                    key = f"{rel}:{match.group(0).lower()}"
                    if key in SANCTIONED:
                        continue
                    found.append(f"{rel}:{line_no}: claims {match.group(0)!r} — {line.strip()[:70]}")
    return found


def main() -> int:
    bad = offenders()
    if bad:
        print("ordering conventions disagree:", file=sys.stderr)
        for line in bad:
            print(f"  {line}", file=sys.stderr)
        print(
            f'\nEvery list a person reads in this stack is "{WANTED}" — the run list, skill\n'
            "history, the review queue, open proposals, waiting memory lines. /v1/audit was\n"
            "the exception, and it was an exception because a JSONL file is appended in that\n"
            "order, not because anyone chose it.\n\n"
            "Either turn it around, or add the phrase to SANCTIONED in this file with the\n"
            "reason. A third opinion held by one service is the thing this exists to stop.",
            file=sys.stderr,
        )
        return 1

    claims = sum(
        path.read_text(encoding="utf-8").lower().count(WANTED)
        for service in SERVICES
        for path in Path(service, service).rglob("*.py")
    )
    print(f'ordering: {claims} lists documented "{WANTED}", {len(SANCTIONED)} sanctioned deviation(s)')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
