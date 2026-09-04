#!/usr/bin/env python3
"""A node kept the promises its brief made — checked from the LEDGER, not the prose.

`assert_dialect.py` checks what the BUS requires of any node: took the handover,
signed its way back, returned something routable. This checks something narrower
and harder — what a particular node promised to do with its own state, which
until now lived only as instructions in a brief and was therefore enforced only
by a model remembering to read them.

It did not remember. On 2026-09-04 at 16:40 a watcher posted a signal for a
conversation and advanced neither of its cursors; the next round read the same
stale baseline and sent the same message again. Nothing noticed, because
"posted a signal" and "wrote the state back" were two sentences in two different
sections of a document, and only the first one had an observable effect.

WHY BEFORE AND AFTER. Three stateless formulations were tried first and all
three PASS on that real defect:

  - "reported must equal feeds"      — both were 16:20:31. Equal. Passes.
  - "reported must exist"            — it existed. Passes.
  - "reported must be within window" — it was. Passes.

The bug was not a cursor lagging another cursor. It was a round advancing
NEITHER, which is invisible unless you know what they were before. So a snapshot
is not an implementation convenience here; it is the only thing that can see the
failure at all.

    assert_node_contract.py --before b.json --after a.json \
        --ledger relay-status.json --since <unix> [--source watch]

Exit 0 when every promise held, 1 otherwise, with the violated promise named in
the words the brief uses — a violation is meant to be readable by whoever wrote
the brief, not only by whoever wrote this.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# How a signal names the conversation it came from. The watcher writes
# `origin: "TypeX / BCP-SRE"`, so the conversation is what follows the
# separator. Kept as a constant with the reason attached rather than inlined:
# this is the one place this checker is coupled to a particular node's signal
# format, and a node that names its subject differently changes THIS line.
ORIGIN_SEPARATOR = " / "

FAILURES: list[str] = []
RAN: list[str] = []


def check(condition: bool, promise: str, detail: str = "") -> None:
    RAN.append(promise)
    if condition:
        print(f"  ok    {promise}")
    else:
        FAILURES.append(f"{promise}{f' — {detail}' if detail else ''}")
        print(f"  FAIL  {promise}{f' — {detail}' if detail else ''}")


def _opt(name: str, default: str = "") -> str:
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def _subjects(ledger: dict[str, Any], source: str, since: float) -> dict[str, float]:
    """Conversations this node produced a signal for, newest signal time each.

    Read from the PIPE's ledger rather than from anything the node reports about
    itself: a node that forgot to write its state is exactly the node whose
    self-report cannot be trusted about whether it did.
    """
    out: dict[str, float] = {}
    for row in ledger.get("recent") or []:
        if row.get("source") != source or float(row.get("received_at") or 0) <= since:
            continue
        origin = str((row.get("fields") or {}).get("origin") or "")
        subject = (
            origin.split(ORIGIN_SEPARATOR, 1)[1].strip()
            if ORIGIN_SEPARATOR in origin
            else ""
        )
        if subject:
            out[subject] = max(
                out.get(subject, 0.0), float(row.get("received_at") or 0)
            )
    return out


def main() -> int:
    positional = [a for a in sys.argv[1:] if not a.startswith("--")]
    named = {"--before", "--after", "--ledger", "--since", "--source"}
    values = {n: _opt(n) for n in named}
    positional = [a for a in positional if a not in values.values()]

    before = json.loads(Path(values["--before"]).read_text(encoding="utf-8"))
    after = json.loads(Path(values["--after"]).read_text(encoding="utf-8"))
    ledger = json.loads(Path(values["--ledger"]).read_text(encoding="utf-8"))
    since = float(values["--since"] or 0)
    source = values["--source"] or "watch"

    b_feeds, b_reported = before.get("feeds") or {}, before.get("reported") or {}
    a_feeds, a_reported = after.get("feeds") or {}, after.get("reported") or {}
    signalled = _subjects(ledger, source, since)

    print(
        f"\nthe round posted {len(signalled)} signal(s) across {len(set(signalled))} conversation(s)"
    )

    # 1. The one that was actually broken.
    stalled = [
        name
        for name in signalled
        if float(a_reported.get(name, 0)) <= float(b_reported.get(name, 0))
        and name in b_reported
    ]
    check(
        not stalled,
        "a conversation it reported has its `reported` cursor moved forward",
        f"posted a signal and left the cursor where it was: {stalled}",
    )

    # 2. Both cursors, not one. Advancing `reported` past `feeds` or leaving
    #    `feeds` behind means the next round re-reads what it just told you about.
    mismatched = [
        (name, a_feeds.get(name), a_reported.get(name))
        for name in signalled
        if name in a_feeds
        and float(a_reported.get(name, 0)) != float(a_feeds.get(name, 0))
    ]
    check(
        not mismatched,
        "a reported conversation ends the round with both cursors on the same timestamp",
        f"{mismatched}",
    )

    # 3. It cannot report more conversations than it looked at.
    advanced = [n for n, v in a_feeds.items() if float(v) > float(b_feeds.get(n, 0))]
    check(
        len(advanced) >= len(signalled),
        "it advanced at least as many conversations as it reported",
        f"advanced {len(advanced)}, reported {len(signalled)}",
    )

    # 4. Structural, and true at every instant rather than only after a round:
    #    you cannot have reported past what you have seen.
    impossible = [
        (n, a_reported[n], a_feeds.get(n))
        for n in a_reported
        if float(a_reported[n]) > float(a_feeds.get(n, 0))
    ]
    check(
        not impossible,
        "no conversation is reported further than it has been read",
        f"{impossible}",
    )

    print()
    if FAILURES:
        print(f"\033[1;31m{len(FAILURES)} promise(s) broken\033[0m")
        print(
            "  Each of these is a sentence in the node's brief. A broken one means the brief "
            "asked and the run did not — \n  which is the failure mode a brief cannot detect about itself."
        )
        return 1
    print(f"\033[1;32mthe node kept its promises\033[0m — all {len(RAN)} of them")
    return 0


if __name__ == "__main__":
    sys.exit(main())
