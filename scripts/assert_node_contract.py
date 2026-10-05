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
        --ledger relay-status.json --since <unix> [--source watch] \
        [--cursors cursors.json] [--signer-ledger signals.jsonl]

Exit 0 when every promise held, 1 otherwise, with the violated promise named in
the words the brief uses — a violation is meant to be readable by whoever wrote
the brief, not only by whoever wrote this.

WHAT MOVED, AND WHY THE PROMISES CHANGED WITH IT. The node used to own both
cursors: `feeds` (how far it had read) and `reported` (what it had actually told
somebody about). Both are now written by different programs — a deterministic
scanner owns `feeds` and writes it to its own file, and the node owns only
`reported`, written by the same call that posts the signal. So two of the four
original promises stopped being promises anybody makes:

  - "both cursors end on the same timestamp" — they cannot. The scanner advances
    `feeds` on every round it reads; `reported` only moves when something was
    worth saying. Equality now means "nothing has happened since", not "the
    round was honest", and keeping the check would fail every healthy round.
  - "it advanced at least as many conversations as it reported" — the scanner
    advances by construction, so this is now true whatever the node did.

What replaced them is a promise the split makes checkable for the first time:
a node can only report a conversation the scan actually OFFERED it. A signal
naming a conversation nobody surfaced is either a mis-copied name — which breaks
this checker's own subject matching, silently — or a round reporting something it
did not read. The offer is one round wide: the scan keeps the round before
alongside (`previous`), because a run that outlasts a tick posts after the file
has moved on, and the signer admits it on the same rule.

WHO WRITES `reported` NOW (2026-10-05). The node's own `reported` cursor was a
self-report — and "a node that forgot to write its state is exactly the node
whose self-report cannot be trusted" is this file's own argument for reading
signals from the pipe's ledger rather than from the node. Since the watcher
posts through `deploy/watch-signer`, that signer records every signal it signed
with the conversation and the cursor the scan offered it at, in a file the
agent cannot reach. With `--signer-ledger` this reads that record instead:

  - "every conversation it reported was signed by the signer" replaces the
    moved-cursor promise. A signal in the pipe with no signer row is a signal
    that went around the boundary, which is the thing worth a page.
  - "no conversation is reported further than it has been read" is checked
    against the signer's recorded cursor.
  - the offered-only promise is unchanged, read from the same pipe ledger.

Without the flag the node's own cursors are read as before — the single-file
shape, and the fixtures the gate replays.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

# How a signal names the conversation it came from. The watcher writes
# `origin: "<tool> / <conversation>"`, so the conversation is what follows the
# separator. Kept as a constant with the reason attached rather than inlined:
# this is the one place this checker is coupled to a particular node's signal
# format, and a node that names its subject differently changes THIS line.
ORIGIN_SEPARATOR = " / "

# The producer half of an origin, for signals THIS CHECK posts. A violation
# travels as a signal on the same source the node uses, so `patrol-timer /
# contract` arrives in the ledger looking exactly like a conversation the node
# reported — and `contract` is never a conversation any scan offered, so the
# next round accuses it, and the round after that accuses that one.
#
# Measured on 2026-09-09: one false positive at 12:20 became two subjects at
# 12:40 and stayed there, firing every twenty minutes. A detector whose own
# output is its next input does not report a problem, it becomes one.
SELF_PRODUCER = "patrol-timer"
# The scanner's own ⚠️ notes travel as a signal with this subject (see
# deploy/watch-signer/signer.py NOTE_SUBJECT): a fault relay, not a
# conversation, so nothing offers it and this does not expect anything to.
NOTE_SUBJECT = "scanner-notes"

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


def _clock(epoch: float) -> str:
    """An epoch as a time a person reads, for the notes this prints."""
    try:
        return time.strftime("%H:%M:%S", time.localtime(epoch))
    except (OverflowError, OSError, ValueError):
        return str(epoch)


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
        if origin.split(ORIGIN_SEPARATOR, 1)[0].strip() == SELF_PRODUCER:
            continue  # our own violation signal; see SELF_PRODUCER
        subject = origin.split(ORIGIN_SEPARATOR, 1)[1].strip() if ORIGIN_SEPARATOR in origin else ""
        if subject and subject != NOTE_SUBJECT:
            out[subject] = max(out.get(subject, 0.0), float(row.get("received_at") or 0))
    return out


def _signed(path: str, since: float) -> dict[str, float | None] | None:
    """What the signer signed since the snapshot: conversation -> the cursor it
    recorded (None when it recorded none). None when no ledger was named."""
    if not path:
        return None
    signed: dict[str, float | None] = {}
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        print(f"  note  no signer ledger yet ({exc}); nothing signed since the snapshot")
        return signed
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("event") != "signal.signed" or float(row.get("ts") or 0) <= since:
            continue
        # Rows written before 2026-10-05 carry no `subject`; the origin they do
        # carry names the same conversation, by the same separator. Read it the
        # same way, or every signal signed before the field existed looks like
        # one that went around the boundary.
        origin = str(row.get("origin") or "")
        fallback = origin.split(ORIGIN_SEPARATOR, 1)[1].strip() if ORIGIN_SEPARATOR in origin else ""
        subject = str(row.get("subject") or fallback)
        if not subject or subject == NOTE_SUBJECT:
            continue
        cursor = row.get("cursor")
        previous = signed.get(subject)
        if cursor is None:
            signed.setdefault(subject, None)
        else:
            signed[subject] = max(float(cursor), previous) if previous is not None else float(cursor)
    return signed


def main() -> int:
    positional = [a for a in sys.argv[1:] if not a.startswith("--")]
    named = {"--before", "--after", "--ledger", "--since", "--source", "--cursors", "--signer-ledger"}
    values = {n: _opt(n) for n in named}
    positional = [a for a in positional if a not in values.values()]

    before = json.loads(Path(values["--before"]).read_text(encoding="utf-8"))
    after = json.loads(Path(values["--after"]).read_text(encoding="utf-8"))
    ledger = json.loads(Path(values["--ledger"]).read_text(encoding="utf-8"))
    since = float(values["--since"] or 0)
    source = values["--source"] or "watch"

    # `feeds` lives with whoever writes it. A node that still keeps both cursors
    # in one file needs no --cursors and is checked exactly as before; one whose
    # reading was split out points at the scanner's file. `offered` is what that
    # scan handed the node THIS round, and is absent for the single-file case —
    # where the promise it backs cannot be checked and is skipped rather than
    # asserted against an empty set, which would fail every round.
    #
    # A missing file is not a violation. The scan writes it on its first round
    # inside the working window, so between a deploy and that round the path is
    # configured and absent — and this runs from a timer that turns a non-zero
    # exit into a signal, so crashing here would page somebody about a file that
    # is merely not written yet. Falls back to the single-file reading, which
    # then finds no `offered` and skips the promise that needs one.
    cursors: dict[str, Any] = {}
    if values["--cursors"]:
        try:
            cursors = json.loads(Path(values["--cursors"]).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"  note  no scan cursors yet ({exc}); checking the node's own file")
    b_reported = before.get("reported") or {}
    a_reported = after.get("reported") or {}
    a_feeds = cursors.get("feeds") or after.get("feeds") or {}
    offered = cursors.get("offered")
    signalled = _subjects(ledger, source, since)
    signed = _signed(values["--signer-ledger"], since)

    print(f"\nthe round posted {len(signalled)} signal(s) across {len(set(signalled))} conversation(s)")

    if signed is not None:
        # 1. The record is the signer's. A signal the pipe took for this node
        #    that the signer never signed went around the boundary.
        unsigned = [name for name in signalled if name not in signed]
        check(
            not unsigned,
            "every conversation it reported was signed by the signer",
            f"signals in the pipe with no signer row: {unsigned}",
        )
    else:
        # 1. The one that was actually broken, for a node that keeps its own books.
        stalled = [
            name
            for name in signalled
            if float(a_reported.get(name, 0)) <= float(b_reported.get(name, 0)) and name in b_reported
        ]
        check(
            not stalled,
            "a conversation it reported has its `reported` cursor moved forward",
            f"posted a signal and left the cursor where it was: {stalled}",
        )

    # 2. It can only report what it was handed. A subject nobody offered is
    #    either a conversation name copied wrong — which breaks the matching in
    #    _subjects() above, so the FIRST promise would go quiet rather than
    #    fail — or a round reporting something it never read.
    #
    #    Skipped, not asserted, when the scan file carries no `offered`: a node
    #    that still keeps one state file has no scan to be handed anything by,
    #    and checking it against an empty set would fail every round it works.
    #
    #    Skipped ALSO when the scan has run again since the snapshot was taken.
    #    `scan.json` has one slot and every tick overwrites it, including the
    #    ticks whose round is skipped — so the moment a round is skipped, the
    #    offered set that justified the PREVIOUS round's signals is gone, and
    #    what remains describes a round that posted nothing. Comparing one
    #    round's signals against another round's offer is not a promise the node
    #    broke; it is two different questions asked as one.
    #
    #    Measured on 2026-09-09: a real round fired at 11:40 and reported a
    #    conversation. The next three ticks all had nothing to do, so all three
    #    rewrote `offered` to empty while the snapshot stayed at 11:40 — and the
    #    check accused that 11:40 round once every twenty minutes, against an
    #    offer made eighty-nine minutes after it.
    #    Since 2026-10-05 the scan keeps the round before alongside, so the
    #    skip has a narrower shape: when the scan has moved on exactly once, the
    #    round before IS the snapshot's round and is checked; only a scan two
    #    or more ticks ahead leaves nothing to check against. And a round that
    #    has not moved on is checked against both, because a run that crossed
    #    the tick posts a conversation it was handed in the round before.
    round_at = float(cursors.get("round_at") or 0)
    previous = cursors.get("previous") if isinstance(cursors.get("previous"), dict) else {}
    previous_offered = previous.get("offered") if isinstance(previous.get("offered"), dict) else None
    previous_at = float(previous.get("round_at") or 0)
    stale_offer = round_at > since > 0
    admissible: dict[str, Any] | None = None
    if offered is None:
        pass
    elif not stale_offer:
        admissible = {**(previous_offered or {}), **offered}
    elif previous_offered is not None and 0 < previous_at <= since:
        admissible = previous_offered
        print(
            f"  note  the scan has run again since the snapshot ({_clock(round_at)} > {_clock(since)}); "
            f"checking against the round before ({_clock(previous_at)})"
        )
    else:
        print(
            "  note  the scan has run again since the snapshot "
            f"({_clock(round_at)} > {_clock(since)}); its offer describes a later round, "
            "so the promise that needs it is skipped"
        )
    if admissible is not None:
        invented = [name for name in signalled if name not in admissible]
        check(
            not invented,
            "every conversation it reported was one the scan offered it",
            f"reported a conversation nothing surfaced: {invented} (offered: {sorted(admissible)})",
        )

    # 3. Structural, and true at every instant rather than only after a round:
    #    you cannot have reported past what you have seen.
    #
    #    Only for conversations the reader still tracks. A name that has a
    #    `reported` entry and no `feeds` one is not a node reporting ahead of
    #    itself — it is a conversation that has since been excluded, renamed, or
    #    dropped off the feed list, leaving stale bookkeeping behind. Treating
    #    the missing side as zero made every one of those a permanent violation.
    reported_now: dict[str, Any] = (
        {n: c for n, c in signed.items() if c is not None} if signed is not None else a_reported
    )
    impossible = [
        (n, reported_now[n], a_feeds[n])
        for n in reported_now
        if n in a_feeds and float(reported_now[n]) > float(a_feeds[n])
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
