#!/usr/bin/env python3
"""Every watch signal in the pipe came through the signer.

The watcher posts through the watch signer (`deploy/sidecars/signer.py`), which holds the door's secret
and records every signal it signed with the conversation it named. So a signal
that reached the pipe's watch door and is NOT in the signer's ledger went
around that boundary: something else holds the secret, or the signer was
bypassed. That is the one thing worth a page about the watcher's bookkeeping,
and the only promise this checks.

It replaced `scripts/assert_node_contract.py` on 2026-10-06. That checker read
the node's own `reported` cursor before and after a round and judged three
promises — written on 2026-09-04, when the node kept its own books and a round
had posted a signal and moved nothing. Once the signer became the record of
what was reported, two of the three held by construction and the third became
this one. The before/after snapshot, the node-state mount it read, seven
environment variables and the fixtures of that September round went with it.

    assert_watch_signed.py --ledger status.json --signer-ledger signals.jsonl \\
        --since <unix> [--source watch]

Exit 0 when every signal since `--since` was signed, 1 otherwise with a FAIL
line naming the conversations; the timer turns that line into a `low` signal.
Signals that name no conversation (a laptop poster's bare origin), the
timer's own (`patrol-timer / …`) and the scanner's own notes (`scanner-notes`)
are not conversations and are not expected in the signer's ledger.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# How a signal names the conversation it came from: `<producer> / <conversation>`.
# deploy/watch/watch_report.py builds it and deploy/sidecars/signer.py
# checks it; this reads the conversation back out of it.
ORIGIN_SEPARATOR = " / "
# The timer posts its own findings under this producer, through its own file
# and never through the signer; the signer refuses the name for that reason.
SELF_PRODUCER = "patrol-timer"
# The scanner's own ⚠️ notes travel with this subject (signer.py NOTE_SUBJECT):
# a fault relay, not a conversation.
NOTE_SUBJECT = "scanner-notes"


def _opt(name: str, default: str = "") -> str:
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def signalled(ledger: dict[str, Any], source: str, since: float) -> set[str]:
    """Conversations the pipe took a signal for, from this source, since the stamp."""
    out: set[str] = set()
    for row in ledger.get("recent") or []:
        if row.get("source") != source or float(row.get("received_at") or 0) <= since:
            continue
        origin = str((row.get("fields") or {}).get("origin") or "")
        if ORIGIN_SEPARATOR not in origin:
            continue
        producer, subject = (part.strip() for part in origin.split(ORIGIN_SEPARATOR, 1))
        if producer == SELF_PRODUCER or not subject or subject == NOTE_SUBJECT:
            continue
        out.add(subject)
    return out


def signed(path: Path, since: float) -> set[str]:
    """Conversations the signer signed a signal for since the stamp.

    Rows written before 2026-10-05 carry `origin` and no `subject`; the origin
    names the conversation by the same separator, so it is read the same way.
    """
    out: set[str] = set()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("event") != "signal.signed" or float(row.get("ts") or 0) <= since:
            continue
        origin = str(row.get("origin") or "")
        fallback = origin.split(ORIGIN_SEPARATOR, 1)[1].strip() if ORIGIN_SEPARATOR in origin else ""
        subject = str(row.get("subject") or fallback)
        if subject:
            out.add(subject)
    return out


def main() -> int:
    ledger = json.loads(Path(_opt("--ledger")).read_text(encoding="utf-8"))
    since = float(_opt("--since") or 0)
    source = _opt("--source") or "watch"
    posted = signalled(ledger, source, since)
    unsigned = sorted(posted - signed(Path(_opt("--signer-ledger")), since))
    print(f"the pipe took {len(posted)} watch signal(s) naming a conversation since the last check")
    if unsigned:
        print(f"  FAIL  every conversation it reported was signed by the signer — not these: {unsigned}")
        return 1
    print("  ok    every conversation it reported was signed by the signer")
    return 0


if __name__ == "__main__":
    sys.exit(main())
