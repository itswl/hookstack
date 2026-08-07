#!/usr/bin/env python3
"""The stack ran — did it produce the right answer?

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
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

FAILURES: list[str] = []


def check(condition: bool, message: str, detail: str = "") -> None:
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

    if len(rows) >= 4:
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

    if sink:
        print("\ndownstream")
        check("飞书卡片" in sink, "the sink received a rendered Feishu card")
        check("[green]" in sink, "the recovery card is green")
        check("已恢复" in sink, "the recovery card says so in its headline")
        check("msgtype" in sink, "a second dialect was rendered from the same judgement")

        # Four judgements dressed for two channels. More than eight means a
        # delivery was retried, and a retry that the downstream already
        # received is a duplicate alert to whoever is on call. The stand-in
        # caused exactly this by being single-threaded while the pipe delivers
        # to channels in parallel.
        deliveries = sink.count("delivery on")
        check(
            deliveries == 8,
            "no downstream received the same alert twice",
            f"expected 8 deliveries, saw {deliveries}",
        )

    print()
    if FAILURES:
        print(f"\033[1;31m{len(FAILURES)} assertion(s) failed\033[0m")
        return 1
    print("\033[1;32mevery stack assertion passed\033[0m")
    return 0


if __name__ == "__main__":
    sys.exit(main())
