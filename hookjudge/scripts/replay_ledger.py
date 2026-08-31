"""What would THIS configuration have served last month?

Reads judged alerts back out of the ledger, asks the judge as the environment
now configures it — model, prompt limits, structured-output dialect — and
diffs the answers against what production actually delivered. The rule-reuse
note said it first: turning a knob here is a measurement, not a config change.
This is the instrument for the knobs the golden set does not cover, pointed at
the one corpus every deployment already owns — its own retained traffic.

Three refusals, each load-bearing:

  * It is NOT a gate, and a flip never exits red. The recorded verdicts are
    the old configuration's own answers, not labels — a gate built on them
    would grade new homework against old homework and call the difference an
    error. The golden set (scripts/eval.py --gate) owns the deploy gate,
    because a person reviewed its expectations. The exception is the handful
    of rows a person actually ruled on (label_importance, mattered): those ARE
    labels, and disagreement with them leads the report.
  * Only route='ai' firings are replayed. A rule-floor verdict and a model
    verdict disagree almost by construction, so replaying floor rows measures
    the distance between a keyword list and a model — already known, not news.
    Recoveries are skipped for the inverse reason: they inherit their firing's
    verdict, so replaying one asks a question this run already asked.
  * The recorded side is never re-drawn. It is what production served, noise
    included; re-judging it would need the old environment and would still
    only buy another draw. The diff answers "how would delivery have
    differed", never "which configuration is right".

One draw of this judge on the same input flips against itself roughly one time
in five (measured on 386 production events: 83% importance / 77% wake
self-agreement — the shadow run's one real finding). A single-draw flip is
therefore likelier the coin than the config, so every first-pass mover is
re-asked: --votes draws, majority per axis, and a move that cannot hold its
majority is reported as the candidate disagreeing with ITSELF, never as a
difference. The bill only pays extra where the first draw moved.

Usage (from the hookjudge directory, the CANDIDATE configuration in the
environment, exactly as the service would read it):

    HOOKJUDGE_AI_MODEL=new-model .venv/bin/python scripts/replay_ledger.py --db /path/to/hookjudge.db
    .venv/bin/python scripts/replay_ledger.py --per-rule 2 --since-days 14 --json

Costs a real model call per draw. The default shape keeps the bill bounded the
way the corpus is actually shaped — a handful of rules carry most of the
volume — by replaying the latest firing of each rule rather than every row,
loudest rules first when --limit cuts.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hookjudge.contract import Incoming
from hookjudge.judge import ai_verdict
from hookjudge.settings import Settings

# Ordered, so "the candidate went lower" is a comparison and not a lookup
# table. Kept in step with scripts/eval.py, which states the same scale.
SEVERITY = ["low", "medium", "high", "critical"]

_ROW_COLUMNS = (
    "id, received_at, source, rule_key, level, title, body, fields_json,"
    " importance, wake_someone, label_importance, mattered"
)


def open_ledger(path: str) -> sqlite3.Connection:
    """Read-only at the connection, not by discipline.

    A replay may point at a LIVE ledger, and this tool must never hold a write
    it could misfire — the rows it would corrupt are the record the diff is
    measured against. mode=ro makes the refusal sqlite's, not a convention.
    """
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    return db


def pick_rows(db: sqlite3.Connection, *, since: float, per_rule: int, limit: int) -> list[dict[str, Any]]:
    """The latest AI-judged firing(s) of each rule, loudest rules first.

    Volume is counted across EVERY route, not just ai: a rule whose one paid
    verdict answered two hundred reuse rows is a two-hundred-row rule, and a
    cap that ranked it behind quieter rules would spend the replay bill where
    delivery never goes. Rows without a rule_key fall back to their title, so
    an upstream that never names its rule still gets replayed rather than
    silently dropped.
    """
    rows = [
        dict(r)
        for r in db.execute(
            f"SELECT {_ROW_COLUMNS} FROM judgements"  # nosec B608 — constant column list
            " WHERE route = 'ai' AND is_recovery = 0 AND received_at >= ?"
            " ORDER BY received_at DESC",
            (since,),
        )
    ]
    volume: Counter[str] = Counter(
        (r["rule_key"] or r["title"] or "").strip()
        for r in db.execute("SELECT rule_key, title FROM judgements WHERE received_at >= ?", (since,))
    )
    picked: list[dict[str, Any]] = []
    taken: Counter[str] = Counter()
    for row in rows:
        key = (row["rule_key"] or row["title"] or "").strip() or f"row-{row['id']}"
        if per_rule and taken[key] >= per_rule:
            continue
        taken[key] += 1
        row["rule"] = key
        row["seen"] = volume[key] or 1
        picked.append(row)
    picked.sort(key=lambda r: -r["seen"])
    return picked[:limit] if limit else picked


def event_of(row: dict[str, Any]) -> Incoming:
    try:
        fields = json.loads(row["fields_json"] or "{}")
    except ValueError:
        fields = {}
    return Incoming.parse(
        {
            "source": row["source"],
            "title": row["title"],
            "body": row["body"],
            "level": row["level"],
            "fields": {str(k): str(v) for k, v in dict(fields).items()},
        },
        # The row's own clock, so identity and rule_key come out the way
        # production computed them, not relative to when the replay ran.
        now=float(row["received_at"]),
    )


def _axis(value: Any) -> str:
    return str(value or "").strip().lower()


def moved(row: dict[str, Any], verdict: Any) -> bool:
    """Did one draw differ from the record on an axis delivery reads?"""
    if verdict.degraded_reason:
        return False
    if _axis(verdict.importance) != _axis(row["importance"]):
        return True
    recorded_wake = _axis(row["wake_someone"])
    got_wake = _axis(verdict.wake_someone)
    return recorded_wake in ("yes", "no") and got_wake in ("yes", "no") and got_wake != recorded_wake


def diff_row(row: dict[str, Any], draws: list[Any]) -> dict[str, Any]:
    """One row's verdict on the candidate, decided by the majority of draws.

    No majority is an answer too: a candidate that cannot repeat itself on a
    row has not disagreed with production, it has disagreed with itself, and
    the report keeps those apart — sending a person to read a coin flip is
    this tool's whole failure mode. A candidate wake of '' is unanswered, not
    quiet: delivery fails open into a card on '', so it is counted separately
    and never as a change.
    """
    recorded_imp = _axis(row["importance"])
    recorded_wake = _axis(row["wake_someone"])
    answered = [d for d in draws if not d.degraded_reason]
    need = len(answered) // 2 + 1

    imp = wake = ""
    steady_imp = steady_wake = False
    if answered:
        (imp, imp_votes), *_ = Counter(_axis(d.importance) for d in answered).most_common(1)
        steady_imp = imp_votes >= need
        (wake, wake_votes), *_ = Counter(_axis(d.wake_someone) for d in answered).most_common(1)
        steady_wake = wake_votes >= need

    imp_changed = steady_imp and imp != recorded_imp
    delta = 0
    if imp_changed and imp in SEVERITY and recorded_imp in SEVERITY:
        delta = SEVERITY.index(imp) - SEVERITY.index(recorded_imp)
    wake_scored = recorded_wake in ("yes", "no")
    wake_changed = wake_scored and steady_wake and wake in ("yes", "no") and wake != recorded_wake
    flip = imp_changed or wake_changed
    samples = [f"{_axis(d.importance)}/{_axis(d.wake_someone) or '-'}" for d in answered]
    wobbled = len(set(samples)) > 1

    label = _axis(row["label_importance"])
    label_delta = 0
    if label in SEVERITY and steady_imp and imp in SEVERITY and imp != label:
        label_delta = SEVERITY.index(imp) - SEVERITY.index(label)
    return {
        "id": row["id"],
        "rule": row["rule"],
        "seen": row["seen"],
        "recorded": f"{recorded_imp}/{recorded_wake or '-'}",
        "candidate": f"{imp or '?'}/{wake or '-'}",
        "samples": samples,
        "flip": flip,
        "delta": delta,
        "new_quiet": wake_changed and wake == "no",
        "new_wake": wake_changed and wake == "yes",
        # First draw moved, the majority did not hold it — the coin, not the config.
        "unsteady": wobbled and not flip,
        "wake_unanswered": wake_scored and steady_wake and wake == "",
        "degraded": len(draws) - len(answered),
        # Only rows a person ruled on carry these; they are the report's headline.
        "label": label,
        "label_delta": label_delta,
        "mattered_quiet": _axis(row["mattered"]) == "yes" and wake_changed and wake == "no",
        "cost": round(sum(d.cost for d in draws), 6),
        "tokens": sum(d.tokens_in + d.tokens_out for d in draws),
    }


async def replay(
    rows: list[dict[str, Any]],
    settings: Settings,
    client: Any,
    *,
    votes: int,
    concurrency: int,
    judge: Any = ai_verdict,
) -> list[dict[str, Any]]:
    gate = asyncio.Semaphore(max(1, concurrency))

    async def draw(row: dict[str, Any]) -> Any:
        async with gate:
            return await judge(client, settings, event_of(row))

    draws: list[list[Any]] = [[v] for v in await asyncio.gather(*(draw(r) for r in rows))]
    movers = [n for n, (row, first) in enumerate(zip(rows, (d[0] for d in draws), strict=True)) if moved(row, first)]
    if votes > 1 and movers:
        extra = await asyncio.gather(*(draw(rows[n]) for n in movers for _ in range(votes - 1)))
        cursor = iter(extra)
        for n in movers:
            draws[n] += [next(cursor) for _ in range(votes - 1)]
    return [diff_row(row, row_draws) for row, row_draws in zip(rows, draws, strict=True)]


def summarize(diffs: list[dict[str, Any]], *, model: str, window_days: float, votes: int) -> dict[str, Any]:
    flips = [d for d in diffs if d["flip"]]
    total_seen = sum(d["seen"] for d in diffs) or 1
    return {
        "model": model,
        "window_days": window_days,
        "votes": votes,
        "rows": len(diffs),
        "rules": len({d["rule"] for d in diffs}),
        "flips": len(flips),
        "quieter": sum(1 for d in flips if d["delta"] < 0),
        "louder": sum(1 for d in flips if d["delta"] > 0),
        "new_quiet": sum(1 for d in diffs if d["new_quiet"]),
        "new_wake": sum(1 for d in diffs if d["new_wake"]),
        "unsteady": sum(1 for d in diffs if d["unsteady"]),
        "wake_unanswered": sum(1 for d in diffs if d["wake_unanswered"]),
        "degraded_draws": sum(d["degraded"] for d in diffs),
        "label_disagreements": sum(1 for d in diffs if d["label_delta"]),
        "label_below": sum(1 for d in diffs if d["label_delta"] < 0),
        "mattered_quiet": sum(1 for d in diffs if d["mattered_quiet"]),
        # Volume-weighted, because three rules are most of any real window: a
        # flip on the loudest rule moves delivery more than five quiet ones.
        "traffic_share_changed": round(sum(d["seen"] for d in flips) / total_seen, 4),
        "cost_total": round(sum(d["cost"] for d in diffs), 6),
        "tokens_total": sum(d["tokens"] for d in diffs),
    }


def render(summary: dict[str, Any], diffs: list[dict[str, Any]]) -> str:
    lines = [
        "",
        f"  Replayed {summary['rows']} firing(s) across {summary['rules']} rule(s), last "
        f"{summary['window_days']:g} day(s), candidate model {summary['model']} — majority of "
        f"{summary['votes']} on movers",
        "",
    ]
    ruled = [d for d in diffs if d["label_delta"] or d["mattered_quiet"]]
    if ruled:
        lines.append("  AGAINST A HUMAN RULING — read these before anything below")
        for d in ruled:
            what = f"person ruled {d['label'] or 'mattered'}, candidate answered {d['candidate']}"
            if d["mattered_quiet"]:
                what += "  (a person said this interruption was worth it; the candidate would drop the card)"
            lines.append(f"    {d['rule'][:40]:<40} {what}")
        lines.append("")
    for d in (d for d in diffs if d["flip"]):
        tag = " NEW QUIET" if d["new_quiet"] else (" new wake" if d["new_wake"] else "")
        lines.append(f"    {d['rule'][:40]:<40} {d['seen']:>5}  {d['recorded']} -> {d['candidate']}{tag}")
    if summary["flips"]:
        lines.append("")
    lines.append(
        f"  {summary['flips']} confirmed difference(s) "
        f"({summary['quieter']} quieter, {summary['louder']} louder, "
        f"{summary['new_quiet']} new-quiet, {summary['new_wake']} new-wake), carrying "
        f"{summary['traffic_share_changed']:.0%} of the window's traffic."
    )
    if summary["unsteady"]:
        lines.append(
            f"  {summary['unsteady']} row(s) moved on the first draw and did not hold a majority — "
            "the candidate disagreeing with itself, not with production."
        )
    if summary["votes"] == 1 and summary["flips"]:
        lines.append(
            "  Single draw per row: roughly one answer in five flips against itself on this judge, "
            "so re-run with --votes 3 before believing any line above."
        )
    if summary["degraded_draws"]:
        lines.append(
            f"  {summary['degraded_draws']} draw(s) degraded to the rule floor — a candidate that "
            "cannot answer is its own finding."
        )
    lines += [
        f"  spent: ${summary['cost_total']:.4f}, {summary['tokens_total']} tokens",
        "",
        "  Not a gate: recorded verdicts are the old configuration's answers, not labels.",
        "  The golden set (eval.py --gate) decides deploys; the human-ruled lines above are",
        "  the only rows here a person has actually ruled on.",
        "",
    ]
    return "\n".join(lines)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default="", help="judgements database (default: HOOKJUDGE_DB, else hookjudge.db)")
    parser.add_argument("--since-days", type=float, default=30.0)
    parser.add_argument("--per-rule", type=int, default=1, help="latest N ai firings per rule; 0 = every ai row")
    parser.add_argument("--limit", type=int, default=0, help="cap on replayed rows, loudest rules kept first")
    parser.add_argument("--votes", type=int, default=3, help="draws on rows whose first draw moved")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--out", type=Path, help="write summary + flips (ids and rule keys, never alert text)")
    args = parser.parse_args()

    settings = Settings.load()
    if not settings.ai_api_key or not settings.ai_base_url:
        print("HOOKJUDGE_AI_API_KEY / HOOKJUDGE_AI_BASE_URL are empty; the candidate needs a provider", file=sys.stderr)
        return 2
    db_path = args.db or settings.db_path
    if not Path(db_path).is_file():
        print(f"no ledger at {db_path} — point --db at a judgements database", file=sys.stderr)
        return 2
    db = open_ledger(db_path)
    try:
        rows = pick_rows(
            db,
            since=time.time() - args.since_days * 86400,
            per_rule=max(0, args.per_rule),
            limit=max(0, args.limit),
        )
    finally:
        db.close()
    if not rows:
        print("nothing to replay: no ai-routed firings in the window", file=sys.stderr)
        return 2

    async with httpx.AsyncClient() as client:
        diffs = await replay(rows, settings, client, votes=max(1, args.votes), concurrency=args.concurrency)
    summary = summarize(diffs, model=settings.ai_model, window_days=args.since_days, votes=max(1, args.votes))

    if args.json:
        print(json.dumps({"summary": summary, "rows": diffs}, indent=2, ensure_ascii=False))
    else:
        print(render(summary, diffs))
    if summary["tokens_total"] and not summary["cost_total"]:
        print(
            "cost reads 0: HOOKJUDGE_AI_PRICE_IN_PER_1K / _OUT_PER_1K are unset.",
            file=sys.stderr,
        )
    if args.out:
        # Ids, rule keys and aggregates only — no alert text, so a result file
        # is safe to hand around; the same rule eval.py's --out keeps.
        kept = ("id", "rule", "seen", "recorded", "candidate", "samples", "delta", "label", "label_delta")
        flips = [{k: d[k] for k in kept} for d in diffs if d["flip"] or d["label_delta"] or d["mattered_quiet"]]
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps({"summary": summary, "flips": flips}, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
