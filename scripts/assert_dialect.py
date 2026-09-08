#!/usr/bin/env python3
"""The async-node dialect held — measured from the BUS, not from the node.

`assert_stack.py` is the other half and answers a different question. It reads
hookjudge's own `/status`: judged counts, the ai/reuse/recovery route split,
priced tokens, identity, recovery inheritance. Every one of those is a property
of THAT implementation. None is in the dialect, which asks a node for exactly
two things: take a normalized event at your door, and post a processed-event
back to your return URL with a valid timestamped HMAC.

The distinction is the point. hookstack's stated acceptance test is "swap
hookjudge for a twenty-line judge of your own and the stack smoke is still
green" — and it could not pass, because the smoke asserted `summary.cost > 0`
and a reuse-route count, which a conforming twenty-line node has no reason to
have. A test that measures the reference implementation cannot tell you the
reference implementation is replaceable.

So everything here reads the PIPE's ledger and the downstream sink: what the bus
handed over, what came back, and whether the loop closed. Any node that speaks
the dialect passes; hookjudge is merely the one that happens to be plugged in.

    python3 scripts/assert_dialect.py <relay-status.json> [sink.log] \
        [--return-door judge-notify] [--brain-channel to-judge] [--events 4]

Run it against your own node by naming its door and channel. If it passes, the
pipe could not tell your node from hookjudge — which is the whole claim.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Same discipline and the same scar as assert_stack.py: most of these sit behind
# a guard, and a guard that does not hold produces no output at all, which looks
# exactly like success. Change this and the count in STACK.md together.
EXPECTED_ASSERTIONS = 8

FAILURES: list[str] = []
RAN: list[str] = []
SKIPPED: list[str] = []


def check(condition: bool, message: str, detail: str = "") -> None:
    RAN.append(message)
    if condition:
        print(f"  ok    {message}")
    else:
        FAILURES.append(f"{message}{f' — {detail}' if detail else ''}")
        print(f"  FAIL  {message}{f' — {detail}' if detail else ''}")


def _opt(name: str, default: str) -> str:
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def main() -> int:
    # Options are name/value pairs, so drop both halves before reading the
    # positionals. By index rather than by value: a sink log could legitimately
    # be named the same as an option's value, and dropping it by value would
    # then silently leave the check reading only the ledger.
    consumed: set[int] = set()
    for index, arg in enumerate(sys.argv[1:], start=1):
        if arg in ("--return-door", "--brain-channel", "--events"):
            consumed.update({index, index + 1})
    positional = [a for i, a in enumerate(sys.argv[1:], start=1) if i not in consumed]

    ledger = json.loads(Path(positional[0]).read_text(encoding="utf-8"))
    sink = Path(positional[1]).read_text(encoding="utf-8", errors="replace") if len(positional) > 1 else ""

    return_door = _opt("--return-door", "judge-notify")
    brain_channel = _opt("--brain-channel", "to-judge")
    expected = int(_opt("--events", "4"))

    rows = ledger["recent"]
    queue = ledger["queue"]
    returns = [r for r in rows if r["source"] == return_door]
    outbound = [r for r in rows if r["source"] != return_door]

    print("\nthe handover out")
    handed = [r for r in outbound if brain_channel in (r.get("channels") or [])]
    check(
        len(handed) == expected,
        f"every front-door event was handed to the node ({brain_channel})",
        f"expected {expected}, routed {len(handed)} of {len(outbound)}",
    )
    # A node that refused, timed out or 401'd on a signature leaves its
    # deliveries in the dead letter queue rather than the ledger being short.
    check(
        queue.get("dead") == 0,
        "the node accepted every delivery",
        f"dead letters: {queue.get('dead')} — the node refused, timed out, or rejected the signature",
    )

    print("\nthe handover back")
    # Arrival AT ALL is the signature assertion: the return door verifies a
    # timestamped HMAC and answers 401 otherwise, so a row here is proof the node
    # signed the family's way. There is nothing further to check about it.
    check(
        len(returns) == expected,
        f"every result came back through the return door ({return_door}), signed",
        f"expected {expected}, got {len(returns)}",
    )
    if not returns:
        SKIPPED.append(
            "the shape and routability of the returned results (3 assertions) — nothing came back, "
            "so there was no processed-event to read"
        )
    else:
        # The door's templates read meta.alert_name / analysis.summary /
        # meta.importance. A non-empty title and level here means those paths
        # resolved, which is the only externally checkable claim about the
        # processed-event's SHAPE — a malformed one extracts to empty and
        # renders as a blank card, which is the failure this catches.
        blank_title = [r["id"] for r in returns if not (r.get("title") or "").strip()]
        check(not blank_title, "each result carried a headline the pipe could read", f"blank on ids {blank_title}")
        blank_level = [r["id"] for r in returns if not (r.get("level") or "").strip()]
        check(not blank_level, "each result carried a level the pipe could route on", f"blank on ids {blank_level}")
        unrouted = [(r["id"], r.get("outcome"), r.get("skip_code")) for r in returns if r.get("outcome") != "routed"]
        check(not unrouted, "each result reached a channel instead of ending no_route", f"{unrouted}")

    if not sink:
        SKIPPED.append(
            "the closed loop (2 assertions) — the sink log was empty or unreadable, so nothing was checked "
            "about what any downstream actually received"
        )
    else:
        print("\nthe loop closed")
        cards = sink.count("delivery on")
        check(cards > 0, "the downstream received the node's results as rendered messages", f"saw {cards}")
        # A retry the downstream already received is a duplicate alert to
        # whoever is on call — the failure a single-threaded stand-in caused
        # once while the pipe delivered to channels in parallel.
        check(
            queue.get("sent", 0) >= cards,
            "no downstream received the same message twice",
            f"the pipe recorded {queue.get('sent')} sends and the sink logged {cards} deliveries",
        )

    print()
    if FAILURES:
        print(f"\033[1;31m{len(FAILURES)} dialect assertion(s) failed\033[0m")
        return 1
    if len(RAN) != EXPECTED_ASSERTIONS:
        print(f"\033[1;31m{len(RAN)} of {EXPECTED_ASSERTIONS} assertions ran — the rest were skipped\033[0m")
        for line in SKIPPED:
            print(f"  skipped  {line}")
        if not SKIPPED:
            print(
                f"  no guard was skipped, so the assertion list itself changed — "
                f"update EXPECTED_ASSERTIONS to {len(RAN)} and STACK.md with it"
            )
        return 1
    print(f"\033[1;32mthe node speaks the dialect\033[0m — all {len(RAN)} assertions, none of them about hookjudge")
    return 0


if __name__ == "__main__":
    sys.exit(main())
