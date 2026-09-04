#!/usr/bin/env python3
"""HOOKJUDGE ran — did it produce the right answer?

Every assertion here reads that one service's `/status`: judged counts, the
ai/reuse/recovery route split, priced tokens, identity, recovery inheritance.
They are properties of an IMPLEMENTATION, not of the async-node dialect, and
naming that is not pedantry — it was the reason this family's own acceptance
test could not pass. "Swap hookjudge for a twenty-line judge of your own and the
stack smoke is still green" was checked by a script asserting `summary.cost > 0`
and a reuse count, which a conforming twenty-line node has no reason to have. A
test that measures the reference implementation cannot tell you the reference
implementation is replaceable.

So the dialect half moved to `assert_dialect.py`, which reads the PIPE's ledger
instead and runs against whatever node is plugged in. This file is now skipped
by `STACK_BRAIN=mine`, deliberately: it is the half that is allowed to know
which brain it is talking to.

These are the checks STACK.md tells a reader to make by eye, encoded so nobody
has to. Each corresponds to a defect that actually shipped and looked healthy
from a distance:

  identity collapse   every alert parsed into the same identity, so everything
                      after the first reused one verdict forever — and the
                      near-zero paid ratio read as excellent savings.
  unreachable route   a recovery could never find its firing, so it fell to the
                      rule floor and re-derived a calmer importance: an alert
                      at `high` closing with a recovery at `medium`.
  silent no-op        the stub model started but nothing pointed at it, so
                      every route came back `rule` at $0 and the cost policy
                      looked like it simply did nothing.
  a skipped check     thirteen of the nineteen sit behind a guard, and the
                      caller reads the sink log with `|| true`. An empty log
                      dropped six of them and this script still printed
                      "every stack assertion passed" — a silence that looked
                      exactly like a green run. Hence the count below.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# What a complete run asserts. Stated as a total because most of these live
# behind a guard — `if sink:` and `if len(rows) >= 4:` — and a guard that does
# not hold produces no output at all, which is indistinguishable from success
# unless somebody is counting. Change this number and STACK.md's "Nineteen
# assertions" in the same commit; they are the same fact written twice.
EXPECTED_ASSERTIONS = 19

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


def main() -> int:
    ledger = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    sink = Path(sys.argv[2]).read_text(encoding="utf-8", errors="replace") if len(sys.argv) > 2 else ""

    summary = ledger["summary"]
    rows = {int(r["id"]): r for r in ledger["recent"]}
    routes = {name: stats["count"] for name, stats in summary["routes"].items()}

    print("\nledger")
    check(summary["judged"] == 4, "four events were judged", f"got {summary['judged']}")
    check(routes.get("ai") == 2, "two events were paid for", f"routes={routes}")
    check(routes.get("reuse") == 1, "the restatement reused instead of paying", f"routes={routes}")
    check(routes.get("recovery") == 1, "the recovery route ran at all", f"routes={routes}")
    check(summary["returns"].get("sent") == 4, "all four judgements reached the pipe", f"{summary['returns']}")
    check(summary["cost"] > 0, "the paid route actually priced its tokens", f"cost={summary['cost']}")

    if len(rows) < 4:
        SKIPPED.append(
            f"the recovery contract and identity (7 assertions) — the ledger returned {len(rows)} recent row(s), "
            "so there was no firing/recovery pair to read"
        )
    else:
        firing, recovery = rows[3], rows[4]
        print("\nthe recovery contract")
        check(bool(recovery["is_recovery"]), "the resolve was read as a recovery")
        check(
            recovery["importance"] == firing["importance"],
            "the recovery inherits its firing's importance",
            f"firing={firing['importance']} recovery={recovery['importance']}",
        )
        check(recovery["cost"] == 0, "the recovery cost nothing", f"cost={recovery['cost']}")
        check(
            recovery["summary"] == firing["summary"],
            "the recovery does not contradict its firing",
        )

        print("\nidentity")
        check(
            rows[1]["identity"] == rows[2]["identity"],
            "a restatement shares its original's identity",
        )
        check(
            rows[1]["identity"] != rows[3]["identity"],
            "two different conditions do not share one identity",
            "identity collapsed — the brain is parsing a shape the pipe is not sending",
        )
        check(
            "status=" not in rows[3]["identity"],
            "state is excluded from identity",
            rows[3]["identity"],
        )

    if not sink:
        SKIPPED.append(
            "downstream (6 assertions) — the sink log was empty or unreadable, so nothing was checked about what "
            "any downstream actually received"
        )
    else:
        print("\ndownstream")
        check("feishu card" in sink, "the sink received a rendered Feishu card")
        check("[green]" in sink, "the recovery card is green")
        check("Resolved" in sink, "the recovery card says so in its headline")
        check("msgtype" in sink, "a second dialect was rendered from the same judgement")

        # Four judgements dressed for two channels (8), plus each front-door
        # event copied once to the investigator's stand-in (4). More than
        # twelve means a delivery was retried, and a retry that the downstream
        # already received is a duplicate alert to whoever is on call. The
        # stand-in caused exactly this by being single-threaded while the pipe
        # delivers to channels in parallel.
        deliveries = sink.count("delivery on")
        check(
            deliveries == 12,
            "no downstream received the same alert twice",
            f"expected 12 deliveries, saw {deliveries}",
        )
        check(
            sink.count("delivery on /probe-standin") == 4,
            "every front-door event was copied to the investigator's stand-in",
            f"saw {sink.count('delivery on /probe-standin')}",
        )

    print()
    if FAILURES:
        print(f"\033[1;31m{len(FAILURES)} assertion(s) failed\033[0m")
        return 1

    # Only now is a short count a failure on its own: whatever did run was
    # right, so the remaining question is whether enough of it ran to mean
    # anything. Reporting this as red is the point — the alternative is the
    # green that thirteen skipped assertions used to produce.
    if len(RAN) != EXPECTED_ASSERTIONS:
        print(f"\033[1;31m{len(RAN)} of {EXPECTED_ASSERTIONS} assertions ran — the rest were skipped\033[0m")
        for line in SKIPPED:
            print(f"  skipped  {line}")
        if not SKIPPED:
            # No guard explains it, so the list itself changed. Say so plainly
            # instead of reporting a phantom skip.
            print(
                f"  no guard was skipped, so the assertion list itself changed — "
                f"update EXPECTED_ASSERTIONS to {len(RAN)} and STACK.md with it"
            )
        return 1

    print(f"\033[1;32mevery stack assertion passed\033[0m — all {len(RAN)} of them")
    return 0


if __name__ == "__main__":
    sys.exit(main())
