"""Score the judge against labelled alerts, and price each verdict.

Accuracy on its own is the wrong number for an alert judge. Calling a low alert
high wastes someone's attention; calling a critical one low means nobody was
told. Those are not the same mistake, so they are counted separately: `missed`
is the count of alerts judged less severe than the label, and it is the number
to watch when a change is meant to save money.

Usage (from the hookjudge directory, with the AI credentials in the environment):

    .venv/bin/python scripts/eval.py --dataset eval/dataset.jsonl
    .venv/bin/python scripts/eval.py --route rule --baseline eval/results/ai.json

Never runs in CI: it spends money and needs a provider.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hookjudge.contract import Incoming
from hookjudge.judge import ai_verdict, rule_verdict
from hookjudge.settings import Settings

# Ordered, so "less severe than the label" is a comparison and not a lookup table.
SEVERITY = ["low", "medium", "high", "critical"]


def load_cases(path: Path) -> tuple[list[dict[str, Any]], int]:
    """Labelled cases, plus how many are still waiting for a human.

    Rows imported from a live ledger arrive with the current model's own verdict
    pre-filled as a suggestion. Scoring against those would be the model grading
    its own homework, so they only count once someone has set reviewed: true.
    """
    cases, unreviewed = [], 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        row = json.loads(line)
        if not row.get("reviewed"):
            unreviewed += 1
            continue
        cases.append(row)
    return cases, unreviewed


async def judge_one(client: httpx.AsyncClient, settings: Settings, row: dict[str, Any], route: str) -> dict[str, Any]:
    alert = row["alert"]
    event = Incoming.parse(
        {
            "source": alert.get("source", "unknown"),
            "title": alert.get("title", ""),
            "body": alert.get("body", ""),
            "level": alert.get("level", ""),
            "fields": alert.get("fields", {}),
        },
        now=time.time(),
    )
    started = time.monotonic()
    if route == "rule":
        verdict = rule_verdict(event)
    else:
        verdict = await ai_verdict(client, settings, event)
    expect = row.get("expect", {})
    got_importance = (verdict.importance or "").lower()
    want_importance = (expect.get("importance") or "").lower()
    distance = 0
    if got_importance in SEVERITY and want_importance in SEVERITY:
        distance = SEVERITY.index(got_importance) - SEVERITY.index(want_importance)
    return {
        "id": row.get("id", ""),
        "importance_ok": got_importance == want_importance,
        "event_type_ok": (verdict.event_type or "").lower() == (expect.get("event_type") or "").lower(),
        # Negative means the judge under-called it: the alert that should have
        # woken someone did not.
        "severity_distance": distance,
        "degraded": verdict.degraded_reason or "",
        "cost": verdict.cost,
        # Reported alongside cost because the price variables are optional and
        # default to zero: tokens are the figure that is always true.
        "tokens": verdict.tokens_in + verdict.tokens_out,
        "latency_ms": int((time.monotonic() - started) * 1000),
    }


def summarize(rows: list[dict[str, Any]], unreviewed: int, route: str) -> dict[str, Any]:
    total = len(rows) or 1
    missed = [r for r in rows if r["severity_distance"] < 0]
    over = [r for r in rows if r["severity_distance"] > 0]
    costs = [r["cost"] for r in rows]
    return {
        "route": route,
        "cases": len(rows),
        "unreviewed": unreviewed,
        "importance_accuracy": round(sum(r["importance_ok"] for r in rows) / total, 4),
        "event_type_accuracy": round(sum(r["event_type_ok"] for r in rows) / total, 4),
        # The number that matters most: alerts judged less severe than the truth.
        "missed": len(missed),
        "missed_ids": [r["id"] for r in missed],
        "over_escalated": len(over),
        "degraded": sum(1 for r in rows if r["degraded"]),
        "cost_total": round(sum(costs), 4),
        "cost_per_verdict": round(sum(costs) / total, 6),
        "tokens_total": sum(r["tokens"] for r in rows),
        "tokens_per_verdict": round(sum(r["tokens"] for r in rows) / total, 1),
        "latency_ms_p50": int(statistics.median([r["latency_ms"] for r in rows])) if rows else 0,
    }


def print_delta(now: dict[str, Any], baseline: dict[str, Any]) -> None:
    """Against a previous run, because the absolute number means little alone."""

    def line(key: str, fmt: str, better_is_low: bool = False) -> None:
        a, b = baseline.get(key), now.get(key)
        if a is None or b is None:
            return
        change = b - a
        if abs(change) < 1e-9:
            mark = "  ="
        elif (change < 0) == better_is_low:
            mark = " ok"
        else:
            mark = " !!"
        print(f"{mark} {key:<22} {a:{fmt}} -> {b:{fmt}}  ({change:+{fmt}})")

    print(f"\nvs baseline ({baseline.get('route', '?')}, {baseline.get('cases', 0)} cases)")
    line("importance_accuracy", ".4f")
    line("event_type_accuracy", ".4f")
    line("missed", "d", better_is_low=True)
    line("over_escalated", "d", better_is_low=True)
    line("cost_total", ".4f", better_is_low=True)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="eval/dataset.jsonl", type=Path)
    parser.add_argument("--route", choices=["ai", "rule"], default="ai")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    if not args.dataset.is_file():
        print(f"no dataset at {args.dataset} — see eval/README.md", file=sys.stderr)
        return 2
    cases, unreviewed = load_cases(args.dataset)
    if args.limit:
        cases = cases[: args.limit]
    if not cases:
        print(f"nothing to score: 0 reviewed cases ({unreviewed} awaiting review)", file=sys.stderr)
        return 2

    settings = Settings.load()
    if args.route == "ai" and not settings.ai_api_key:
        print("HOOKJUDGE_AI_API_KEY is empty; --route ai needs a provider", file=sys.stderr)
        return 2

    gate = asyncio.Semaphore(args.concurrency)
    async with httpx.AsyncClient() as client:

        async def run(row: dict[str, Any]) -> dict[str, Any]:
            async with gate:
                return await judge_one(client, settings, row, args.route)

        rows = await asyncio.gather(*(run(row) for row in cases))

    report = summarize(list(rows), unreviewed, args.route)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if unreviewed:
        print(f"\n{unreviewed} row(s) awaiting review and not scored.", file=sys.stderr)
    if args.route == "ai" and report["tokens_total"] and not report["cost_total"]:
        print(
            "\ncost reads 0: HOOKJUDGE_AI_PRICE_IN_PER_1K / _OUT_PER_1K are unset, "
            "so every verdict in the ledger is priced at zero too.",
            file=sys.stderr,
        )
    if args.baseline and args.baseline.is_file():
        print_delta(report, json.loads(args.baseline.read_text(encoding="utf-8")))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        # Ids and aggregates only — no alert text, so a result file is safe to share.
        args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
