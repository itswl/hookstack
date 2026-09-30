#!/usr/bin/env python3
"""What needs you this morning, as ONE signal for the pipe's watch door.

The board's four numbers and the cards waiting on a person, computed from the
pipe's two feeds the way the board computes them, printed as the signal that
`post_watch_signal.py` posts. So the answer arrives where the cards already
are — the chat, on a clock — instead of on a page nobody opens; the parking
note of 2026-09-08 called that the only useful form of an overview.

    HOOKRELAY_READ_TOKEN=… python3 scripts/needs_you.py --relay http://127.0.0.1:8100 \\
      | python3 scripts/post_watch_signal.py

`--json` prints the figures instead of the signal, for a person or a test.
Everything here is the pipe's own account, read by identifiers: a card that
asked (labels the pipe wrote) and no press in its chain is "waiting"; a later
event of the same source and title the source stated as a recovery is "ended",
counted from when it ended; a return whose title says the fix held is "ended"
too. A routed single hop under two hours old is "in flight", and so is a
procedure somebody approved that nothing has answered since. A plan handed off
is in flight until another session reports after it. That report ended well when
it changed something, waits on you when it changed nothing and named what it is
missing, and counts nowhere when it failed or said neither. No alert text is
read.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from typing import Any

QUIET_KINDS = {"tick", "note", "brief", "report"}
PRESS_KINDS = {"card-action"}


def _get(url: str, token: str, timeout: float = 15.0) -> Any:
    headers = {"X-Read-Token": token} if token else {}
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 — operator URL  # nosec B310
        return json.loads(response.read().decode("utf-8", "replace"))


def _kind(row: dict[str, Any]) -> str:
    fields = row.get("fields") or {}
    source = str(row.get("source") or "")
    if source == "absence" or fields.get("door"):
        return "absence"
    if source.endswith("-notify"):
        return "verdict" if source == "judge-notify" else source[: -len("-notify")]
    if source.endswith("-due"):
        return "tick"
    return str(fields.get("kind") or "alert")


def _count(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _work_outcome(fields: dict[str, Any]) -> str:
    """The board's workOutcome, from the two counts the work node states."""
    if fields.get("probe_status") == "failed":
        return "work_failed"
    if _count(fields.get("changed_files")) > 0:
        return "carried_out"
    return "work_blocked" if _count(fields.get("blocked")) > 0 else "work_reported"


def _is_fix(hop: dict[str, Any]) -> bool:
    return str(hop.get("title") or "").endswith(("· fix held", "· fix did not hold"))


def _recovered(rows: dict[int, dict[str, Any]], row: dict[str, Any], at: float) -> dict[str, Any] | None:
    best = None
    for other in rows.values():
        if (
            not other.get("is_recovery")
            or other.get("source") != row.get("source")
            or other.get("title") != row.get("title")
        ):
            continue
        when = float(other.get("received_at") or 0)
        if when <= at or when > at + 86400:
            continue
        if best is None or when < float(best.get("received_at") or 0):
            best = other
    return best


def _approved_unanswered(
    hops: list[dict[str, Any]], presses: list[dict[str, Any]], rows: dict[int, dict[str, Any]]
) -> bool:
    """A person approved the proposed procedure and no report came back after:
    the machine is working on it, and the board draws it in the in-flight blue."""
    approved = [
        float(h.get("at") or 0)
        for h in presses
        if (rows.get(int(h.get("id") or 0), {}).get("fields") or {}).get("kind") == "approve"
    ]
    if not approved:
        return False
    runs = [float(h.get("at") or 0) for h in hops[1:] if h.get("session") and h not in presses]
    return not any(run > press for run in runs for press in approved)


def analyse(timeline: dict[str, Any], status: dict[str, Any], *, now: float, hours: float = 24.0) -> dict[str, Any]:
    """The board's figures, from the same two feeds it reads."""
    rows = {int(r["id"]): r for r in status.get("recent") or [] if r.get("id") is not None}
    waiting: list[dict[str, Any]] = []
    in_flight = ended = quiet = 0
    for chain in timeline.get("chains") or []:
        hops = chain.get("hops") or []
        if not hops:
            continue
        origin = hops[0]
        row = rows.get(int(origin.get("id") or 0), {})
        t0 = float(origin.get("at") or chain.get("started_at") or 0)
        routed = origin.get("outcome") == "routed"
        kind = _kind(row) if row else "alert"
        if row.get("is_recovery") or not routed or (len(hops) == 1 and kind in QUIET_KINDS):
            quiet += 1
            continue
        presses = [
            h
            for h in hops[1:]
            if h.get("door") in PRESS_KINDS
            or h.get("sender")
            or (rows.get(int(h.get("id") or 0), {}).get("fields") or {}).get("kind") == "follow_up"
        ]
        asked: list[str] = []
        asked_at = None
        asked_on: list[str] = []
        for hop in hops:
            labels = hop.get("asked") or []
            if labels and asked_at is None:
                asked_at = float(hop.get("at") or t0)
            for label in labels:
                if label not in asked:
                    asked.append(label)
            for delivery in rows.get(int(hop.get("id") or 0), {}).get("deliveries") or []:
                if delivery.get("asked") and delivery.get("channel") not in asked_on:
                    asked_on.append(str(delivery.get("channel")))
        fix = next((h for h in reversed(hops) if _is_fix(h)), None)
        # A plan handed off joined by its work item (the pipe's `by_work`); the
        # reports after it from another session are the work it started.
        reports = [h for h in hops[1:] if h.get("session") and h not in presses and not _is_fix(h)]
        handoff = next((h for h in reversed(reports) if h.get("by_work")), None)
        work = [
            h
            for h in reports
            if handoff is not None
            and h is not handoff
            and h.get("session") != handoff.get("session")
            and float(h.get("at") or 0) >= float(handoff.get("at") or 0)
        ]
        outcome = _work_outcome(rows.get(int(work[-1].get("id") or 0), {}).get("fields") or {}) if work else ""
        recovery = _recovered(rows, row, t0) if row else None
        # When the chain last MOVED, which for a recovered alert is the
        # recovery: it is a separate event, not a hop, and an alert that fired
        # yesterday and ended an hour ago ended well today.
        last = max(
            [float(h.get("at") or t0) for h in hops] + ([float(recovery.get("received_at") or 0)] if recovery else [])
        )
        if fix and str(fix.get("title") or "").endswith("· fix held") or (recovery and not fix):
            if now - last <= hours * 3600:
                ended += 1
            continue
        if fix:
            continue  # did not hold: on the board in red, not a "waiting" line
        if asked and not presses:
            waiting.append(
                {
                    "chain": chain.get("chain"),
                    "title": str(origin.get("title") or "(untitled)")[:120],
                    "asked": asked,
                    "since": asked_at or t0,
                    "on": asked_on,
                }
            )
        elif outcome == "carried_out":
            if now - last <= hours * 3600:
                ended += 1
        elif outcome == "work_blocked":
            since = float(work[-1].get("at") or t0)
            title = str(origin.get("title") or "(untitled)")[:120]
            waiting.append(
                {"chain": chain.get("chain"), "title": title, "asked": [], "since": since, "on": [], "blocked": True}
            )
        elif outcome:
            pass  # failed: red on the board; reported: it said neither, and the board says so
        elif handoff is not None or _approved_unanswered(hops, presses, rows) or (len(hops) == 1 and now - t0 < 7200):
            in_flight += 1
    queue = status.get("queue") or {}
    waiting.sort(key=lambda w: w["since"])
    return {
        "waiting": waiting,
        "in_flight": in_flight,
        "dead": int(queue.get("dead") or 0),
        "queued": int(queue.get("queued") or 0),
        "ended_well": ended,
        "quiet": quiet,
        "chains": len(timeline.get("chains") or []),
        "hours": hours,
    }


def _ago(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def signal(figures: dict[str, Any], *, now: float, board: str = "") -> dict[str, Any]:
    """The card: one title a person decides from, the lines under it."""
    waiting = figures["waiting"]
    day = time.strftime("%m-%d", time.localtime(now))
    title = f"Needs you · {len(waiting)} waiting · {day}" if waiting else f"Nothing waiting on you · {day}"
    lines = []
    for item in waiting[:8]:
        if item.get("blocked"):
            lines.append(
                f"· {item['title']} — the work run changed nothing {_ago(now - item['since'])} ago "
                f"and named what it is missing  (#{item['chain']})"
            )
            continue
        where = f" on {', '.join(item['on'])}" if item["on"] else ""
        lines.append(
            f"· {item['title']} — asked {_ago(now - item['since'])} ago{where}: "
            f"{' · '.join(item['asked'])[:120]}  (#{item['chain']})"
        )
    if len(waiting) > 8:
        lines.append(f"· and {len(waiting) - 8} more")
    summary = (
        f"in flight {figures['in_flight']} · dead letters {figures['dead']}"
        + (f" ({figures['queued']} queued)" if figures["queued"] else "")
        + f" · ended well in {int(figures['hours'])}h: {figures['ended_well']}"
        + f" · quiet {figures['quiet']} of {figures['chains']} chains"
    )
    detail = "\n".join(lines + [summary] + ([f"board: {board}"] if board else []))[:1800]
    return {"title": title, "detail": detail, "level": "low", "origin": "needs-you", "kind": "report"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--relay", default=os.environ.get("HOOKRELAY_URL", "http://127.0.0.1:8100"))
    ap.add_argument("--hours", type=float, default=24.0, help="the window for 'ended well'")
    ap.add_argument(
        "--board",
        default=os.environ.get("HOOKRELAY_BOARD_URL", ""),
        help="a link to put on the card, if the reader can open one",
    )
    ap.add_argument("--json", action="store_true", help="print the figures, not the signal")
    args = ap.parse_args()
    token = os.environ.get("HOOKRELAY_READ_TOKEN", "")
    base = args.relay.rstrip("/")
    try:
        timeline = _get(f"{base}/timeline?limit=100", token)
        status = _get(f"{base}/status?limit=400", token)
    except Exception as exc:  # noqa: BLE001 — the caller reports; a morning card must not look like a quiet day
        print(f"could not read the pipe at {base}: {exc}", file=sys.stderr)
        return 1
    now = time.time()
    figures = analyse(timeline, status, now=now, hours=args.hours)
    out = figures if args.json else signal(figures, now=now, board=args.board)
    print(json.dumps(out, ensure_ascii=False, indent=2 if args.json else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
