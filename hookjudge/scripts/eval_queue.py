#!/usr/bin/env python3
"""
Order the labelling work by where independent judgements disagree.

Usage:
    python scripts/eval_queue.py --opinions eval/results/op-*.json

Examples:
    # The queue, richest disagreement first
    python scripts/eval_queue.py --opinions /tmp/op-rule.json /tmp/op-a.json /tmp/op-b.json

    # With WebhookWise's outcome facts and investigator verdicts joined in
    python scripts/eval_queue.py --opinions /tmp/op-*.json --ww-evidence /tmp/ww-evidence.json

    # Pseudonymised — the form a public artifact would take
    python scripts/eval_queue.py --opinions /tmp/op-*.json --mask

WHY THIS EXISTS

32 rules, none labelled, and the README is right that the corpus cannot be
labelled by the system under test. But it does not follow that a human must read
all 32 cold. Several independent processes already have an opinion about each
row — the free rule route, each judge on its own model, and (through
WebhookWise's evidence file) an investigator that had tool access and minutes.

Where they all agree, a label is close to free and the human is confirming.
Where they diverge by two severity levels, that row is the whole afternoon's
value. This orders by that, weighted by how much traffic the rule actually
carries: three rules are 87% of the volume in this corpus, so labelling those
three well matters more than the other twenty-nine combined.

WHAT IT DELIBERATELY DOES NOT DO

It does not write labels, and it does not touch the dataset. Unanimity among
cheap judgements is not truth — it is agreement, and agreement among things that
share a training distribution is weaker evidence than it looks. The output says
which rows are cheap to confirm and which need thought; a person still decides,
and `reviewed: true` stays a human act.

It also does not fold the investigator's verdict in as a vote. It is reported in
its own column because it comes from an asymmetric process (searched, read case
files, minutes not milliseconds) and mixing it into a majority with two cheap
judges would throw away exactly the property that makes it worth having.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}
SHORT = {"low": "l", "medium": "m", "high": "h", "critical": "C"}


def _load_dataset(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            rows.append(json.loads(line))
    return rows


def _key(name: str) -> str:
    """Normalise a rule name for joining across services.

    The corpus slugifies ASCII rule names and leaves CJK ones alone, so an exact
    match found 2 of 32 — and one of the misses was the third-largest rule in the
    corpus, which had 25 investigator reports sitting unjoined. Dropping case and
    every non-alphanumeric character is enough to bridge slug and original
    without inventing a matcher that could pair two different rules.
    """
    return "".join(ch for ch in name.lower() if ch.isalnum())


def _spread(values: list[str]) -> int:
    ranks = [RANK[v] for v in values if v in RANK]
    return max(ranks) - min(ranks) if len(ranks) > 1 else 0


def build(
    dataset: list[dict[str, Any]],
    opinions: dict[str, dict[str, Any]],
    evidence: dict[str, Any],
    order: str = "spread",
) -> dict[str, Any]:
    ww_rules = (evidence or {}).get("rules") or {}
    ww_by_key = {_key(name): data for name, data in ww_rules.items()}
    total_seen = sum(int(r.get("seen") or 0) for r in dataset) or 1
    out = []
    for row in dataset:
        rid = str(row.get("id") or "")
        raw = {name: (data["rows"].get(rid) or {}) for name, data in opinions.items()}
        votes = {name: str(o.get("importance") or "") for name, o in raw.items()}
        votes = {k: v for k, v in votes.items() if v}
        # A judge that disagreed with ITSELF across votes. Measured on the shadow
        # deployment at 83%/77% same-model agreement, so roughly one opinion in
        # five is a coin flip — and a row that looks contested may be one judge
        # flipping rather than two judges differing. Sending a person to
        # adjudicate that is sending them to adjudicate noise.
        unstable = sorted(name for name, o in raw.items() if o and not o.get("stable", True))
        ww = ww_rules.get(rid) or ww_by_key.get(_key(rid)) or {}
        investigator = (ww.get("tier2_investigator") or {}).get("verdicts") or {}
        out.append(
            {
                "id": rid,
                "alert": row.get("alert") or {},
                "note": row.get("note") or "",
                "expect": row.get("expect") or {},
                "seen": int(row.get("seen") or 0),
                "reviewed": bool(row.get("reviewed")),
                "votes": votes,
                "spread": _spread(list(votes.values())),
                "unstable": unstable,
                "unanimous": len(set(votes.values())) == 1 and len(votes) > 1,
                # Reported, never voted. See the module docstring.
                "investigator": investigator,
                "excerpts": (ww.get("tier2_investigator") or {}).get("excerpts") or [],
                "outcome": ww.get("tier1_outcome") or {},
                "capped_at": ww.get("capped_at", ""),
            }
        )
    if order == "volume":
        out.sort(key=lambda r: (-r["seen"], -r["spread"]))
    else:
        out.sort(key=lambda r: (-r["spread"], -r["seen"]))
    # Contested means the judges differ AND each was steady about it. A wide
    # spread carried by an unsteady judge is a measurement problem, not a
    # labelling one, and it belongs in a different queue.
    contested = [r for r in out if r["spread"] >= 2 and not r["unstable"]]
    noisy = [r for r in out if r["spread"] >= 2 and r["unstable"]]
    return {
        "rows": out,
        "sources": {name: data.get("model") or data.get("route") for name, data in opinions.items()},
        "summary": {
            "rules": len(out),
            "unanimous": sum(1 for r in out if r["unanimous"]),
            "adjacent": sum(1 for r in out if r["spread"] == 1),
            "contested": len(contested),
            "wide_but_unstable": len(noisy),
            # How much of the corpus has a judge that disagreed with itself. When
            # this is high the spread ordering is measuring the instrument, not
            # the alerts, and the honest thing is to say so rather than rank on it.
            "unstable_share": round(sum(1 for r in out if r["unstable"]) / max(1, len(out)), 4),
            "order": order,
            "contested_volume_share": round(sum(r["seen"] for r in contested) / total_seen, 4),
            "with_investigator": sum(1 for r in out if r["investigator"]),
            "joined_to_ww": sum(1 for r in out if r["outcome"]),
        },
    }


def render(queue: dict[str, Any], mask: bool) -> str:
    names = list(queue["sources"])
    lines = [
        "",
        "  Labelling queue — widest disagreement first, then volume",
        f"  sources: {', '.join(f'{n}={queue["sources"][n]}' for n in names)}",
        "  Nothing here is a label. Unanimity means agreement, not truth.",
        "",
        f"  {'#':>3} {'rule':<26} {'seen':>5} "
        + " ".join(f"{n[:6]:>6}" for n in names)
        + f" {'spr':>3} {'invest':<12} what",
        f"  {'-' * 3} {'-' * 26} {'-' * 5} " + " ".join("-" * 6 for _ in names) + f" {'-' * 3} {'-' * 12} ----",
    ]
    for n, row in enumerate(queue["rows"], 1):
        label = f"rule-{n:02d}" if mask else row["id"][:26]
        votes = " ".join(f"{SHORT.get(row['votes'].get(name, ''), '-'):>6}" for name in names)
        inv = " ".join(f"{SHORT.get(k, k[:1])}:{v}" for k, v in sorted(row["investigator"].items())) or "-"
        if row["spread"] >= 2 and row["unstable"]:
            what = f"unstable ({','.join(row['unstable'])} flipped)"
        elif row["spread"] >= 2:
            what = "ADJUDICATE"
        elif row["unanimous"]:
            what = f"confirm ({len(row['votes'])} agree)"
        elif row["spread"] == 1:
            what = "adjacent"
        else:
            what = "one opinion only"
        if row["capped_at"]:
            what += f" · capped {row['capped_at']}"
        lines.append(f"  {n:>3} {label:<26} {row['seen']:>5} {votes} {row['spread']:>3} {inv:<12} {what}")

    s = queue["summary"]
    unstable_share = s.get("unstable_share", 0.0)
    lines += [
        "",
        f"  {s['contested']} rule(s) genuinely contested (spread >= 2, every judge steady), carrying "
        f"{s['contested_volume_share']:.0%} of the corpus volume.",
        f"  {s.get('wide_but_unstable', 0)} wide but UNSTABLE — a judge disagreeing with itself.",
        f"  {s['unanimous']} unanimous, {s['adjacent']} adjacent-only, "
        f"{s['with_investigator']} have an investigator verdict to weigh against "
        f"({s.get('joined_to_ww', 0)} joined to outcome facts).",
        "",
    ]
    if unstable_share >= 0.2:
        lines += [
            f"  {unstable_share:.0%} of rows have a judge that disagreed with ITSELF, so the",
            "  disagreement axis is measuring the instrument and not the alerts. At this noise",
            "  level a spread ordering cannot be trusted"
            + (" — and this run used it." if s.get("order") == "spread" else "."),
            "",
            "  Order by what does not move: --by volume, read against the investigator",
            "  column. More votes narrows this floor; nothing removes it.",
            "",
        ]
    else:
        lines += [
            "  Adjudicate the contested ones and confirm the unanimous ones. The adjacent rows",
            "  are where cheap judges split between neighbouring levels, which is the least",
            "  informative kind of disagreement and the last worth an afternoon.",
            "",
        ]
    return "\n".join(lines)


def render_brief(queue: dict[str, Any], mask: bool) -> str:
    """One packet per contested row: the alert, every opinion, and the evidence.

    Only the contested ones. A packet per row for all 32 is a document nobody
    finishes, and the rows where three cheap judges already agree do not need
    prose to confirm — that is what the table is for.
    """
    names = list(queue["sources"])
    contested = [r for r in queue["rows"] if r["spread"] >= 2 and not r["unstable"]]
    if not contested:
        return "\n  Nothing contested. The table is the whole story.\n"
    out = [
        "",
        f"  {len(contested)} contested row(s). Read, decide, then edit the dataset:",
        "  set expect.importance, write WHY in note, and set reviewed: true.",
        "",
    ]
    for n, row in enumerate(contested, 1):
        alert = row["alert"]
        label = f"rule-{n:02d}" if mask else row["id"]
        out += [
            "  " + "=" * 74,
            f"  [{n}] {label}    seen {row['seen']}    spread {row['spread']}"
            + ("    (every judge steady)" if not row["unstable"] else ""),
            "  " + "=" * 74,
            f"    source : {alert.get('source', '?')}",
            f"    level  : {alert.get('level') or '(none — and that absence is itself evidence)'}",
            f"    title  : {str(alert.get('title', ''))[:300]}",
        ]
        body = str(alert.get("body", "") or "").strip()
        if body:
            out.append(f"    body   : {body[:600]}")
        fields = alert.get("fields") or {}
        if fields:
            out.append(f"    fields : {', '.join(sorted(fields)[:12])}")
        out.append("")
        out.append("    cheap judgements: " + "  ".join(f"{name}={row['votes'].get(name, '-')}" for name in names))
        outcome = row["outcome"]
        if outcome:
            out.append(
                f"    outcome facts   : {outcome.get('alerts', '?')} alerts/30d, "
                f"{outcome.get('recurring_share', 0):.0%} recurring, "
                f"{outcome.get('forwarded_share', 0):.0%} delivered, "
                f"{outcome.get('distinct_hashes', '?')} distinct identities"
            )
        if row["investigator"]:
            spread = " ".join(f"{k}:{v}" for k, v in sorted(row["investigator"].items()))
            out.append(f"    investigator    : {spread}   <- a process that LOOKED, weigh this heaviest")
        if row["capped_at"]:
            out.append(f"    already capped  : {row['capped_at']} (WebhookWise calibration acted on that evidence)")
        for ex in row["excerpts"][:3]:
            out.append(f"      · [{ex['severity']}] {ex['when']}  {ex['summary'][:300]}")
        if not row["investigator"]:
            out.append("    investigator    : NONE — nothing has looked at this yet.")
            out.append("      Labelling it now records an opinion, not a finding. Consider")
            out.append("      investigating one instance first, or leave it unreviewed.")
        out.append("")
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", default="eval/dataset.jsonl", type=Path)
    parser.add_argument("--opinions", nargs="+", type=Path, required=True, help="files from eval.py --collect")
    parser.add_argument("--ww-evidence", type=Path, help="output of WebhookWise scripts.ops.label_evidence --json")
    parser.add_argument(
        "--mask", action="store_true", help="pseudonymise rule names (the shape a public artifact takes)"
    )
    parser.add_argument(
        "--by",
        choices=["spread", "volume"],
        default="spread",
        help="order by disagreement (default) or by how much traffic the rule carries",
    )
    parser.add_argument(
        "--brief",
        action="store_true",
        help="print the full adjudication packet for the contested rows only",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if not args.dataset.is_file():
        print(f"no dataset at {args.dataset} — see eval/README.md")
        return 2
    opinions = {}
    for path in args.opinions:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not data.get("collected"):
            print(f"{path} is not an opinion file — run eval.py with --collect")
            return 2
        opinions[data.get("model") or data.get("route") or path.stem] = data
    evidence = json.loads(args.ww_evidence.read_text(encoding="utf-8")) if args.ww_evidence else {}

    queue = build(_load_dataset(args.dataset), opinions, evidence, order=args.by)
    if args.json:
        print(json.dumps(queue, indent=2, ensure_ascii=False))
    elif args.brief:
        print(render_brief(queue, args.mask))
    else:
        print(render(queue, args.mask))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
