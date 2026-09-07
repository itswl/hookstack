#!/usr/bin/env python3
"""The week's bill and the week's attention, as one page for whoever pays.

Deterministic on purpose. The family already keeps every number this needs —
the judge's routes and costs, the pipe's priced chains and incidents, the
investigator's runs, budget and rulings — and a governance report is the one
document where an approximate figure is worse than none. So this asks the
three read APIs and does the arithmetic itself; no model is paid to summarise
a ledger, and the same inputs always give the same page.

Two kinds of number are kept apart and labelled:

  measured       what was actually billed and delivered
  counterfactual what the cost policy AVOIDED — free verdicts (recovery,
                 reuse, rule-reuse) and runbook answers, priced at this week's
                 average paid call. An estimate of a bill that did not happen,
                 and it says so.

    HOOKRELAY_READ_TOKEN=... HOOKJUDGE_READ_TOKEN=... HOOKPROBE_TOKEN=... \\
      python3 scripts/cost_report.py --relay http://127.0.0.1:8100 \\
        --judge http://127.0.0.1:8200 --probe http://127.0.0.1:8088 [--hours 168] [--json]

A service that is not given, or cannot be read, gets a section that says so
rather than a zero that looks like a fact. Exit 0 with the page on stdout.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any


def _get(url: str, token: str) -> dict | list | None:
    req = urllib.request.Request(
        url, headers={"X-Read-Token": token, "Authorization": f"Bearer {token}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as res:
            return json.load(res)
    except (urllib.error.URLError, json.JSONDecodeError, OSError):
        return None


def _money(v: Any) -> str:
    try:
        return f"${float(v):.4f}" if float(v) < 1 else f"${float(v):.2f}"
    except (TypeError, ValueError):
        return "—"


def _pct(n: float, d: float) -> str:
    return f"{100.0 * n / d:.0f}%" if d else "—"


def compute(
    judge: dict | None,
    timeline: dict | None,
    budget: dict | None,
    runs: list | None,
    *,
    hours: float,
    now: float,
) -> dict[str, Any]:
    """Every figure the page prints, from the raw API bodies. Pure."""
    report: dict[str, Any] = {"hours": hours, "generated_at": now}

    if isinstance(judge, dict) and isinstance(judge.get("summary"), dict):
        s = judge["summary"]
        routes: dict[str, dict] = s.get("routes") or {}
        paid = int((routes.get("ai") or {}).get("count") or 0)
        judged = int(s.get("judged") or 0)
        cost = float(s.get("cost") or 0.0)
        avg_paid = cost / paid if paid else 0.0
        free_routes = {
            k: int(v.get("count") or 0)
            for k, v in routes.items()
            if k in ("recovery", "reuse", "rule-reuse")
        }
        floor = int((routes.get("rule") or {}).get("count") or 0)
        att = s.get("attention") or {}
        interruptions = int(att.get("interruptions") or 0)
        conditions = int(att.get("conditions") or 0)
        wake_no = int(att.get("wake_no") or 0)
        report["judge"] = {
            "judged": judged,
            "paid": paid,
            "cost": round(cost, 6),
            "avg_paid": round(avg_paid, 6),
            "paid_ratio_pct": s.get("paid_ratio_pct"),
            "free_routes": free_routes,
            "rule_floor": floor,
            "avoided_verdicts": sum(free_routes.values()),
            "avoided_cost": round(sum(free_routes.values()) * avg_paid, 4),
            "attention": {
                "interruptions": interruptions,
                "conditions": conditions,
                "repeats": int(att.get("repeats") or 0),
                "wake_yes": int(att.get("wake_yes") or 0),
                "wake_no": wake_no,
                "likely_flapping": int(att.get("likely_flapping") or 0),
                "mattered": int(att.get("mattered") or 0),
                "did_not_matter": int(att.get("did_not_matter") or 0),
                "ruled": int(att.get("ruled") or 0),
                "quiet_regrets": att.get("quiet_regrets"),
                "delivered_per_condition": round(
                    (interruptions - wake_no) / conditions, 2
                )
                if conditions
                else None,
            },
        }

    if isinstance(timeline, dict):
        chains = timeline.get("chains") or []
        incidents = timeline.get("incidents") or []
        multi = [i for i in incidents if int(i.get("interruptions") or 0) > 1]
        oldest = min((float(c.get("started_at") or now) for c in chains), default=now)
        report["pipe"] = {
            "chains": len(chains),
            "hops": int((timeline.get("totals") or {}).get("hops") or 0),
            "priced_cost": float((timeline.get("totals") or {}).get("cost_usd") or 0.0),
            "unpriced_hops": int(
                (timeline.get("totals") or {}).get("unpriced_hops") or 0
            ),
            "span_days": round((now - oldest) / 86400, 1),
            "incidents_multi": len(multi),
            "top_incidents": sorted(
                incidents, key=lambda i: -float(i.get("cost_usd") or 0)
            )[:5],
        }

    if isinstance(runs, list):
        since = now - hours * 3600
        week = [
            r
            for r in runs
            if float(r.get("finished_at") or 0) >= since
            and r.get("status") != "running"
        ]
        costs = [float(r.get("cost_usd") or 0) for r in week]
        answered = [r for r in week if r.get("answered_from_runbook")]
        paid_runs = [r for r in week if float(r.get("cost_usd") or 0) > 0]
        avg_run = (sum(costs) / len(paid_runs)) if paid_runs else 0.0
        report["investigator"] = {
            "runs": len(week),
            "cost": round(sum(costs), 4),
            "avg_run": round(avg_run, 4),
            "answered_from_runbook": len(answered),
            "avoided_cost": round(len(answered) * avg_run, 4),
            "ruled_useful": sum(1 for r in week if r.get("ruling") == "useful"),
            "ruled_useless": sum(1 for r in week if r.get("ruling") == "useless"),
            "unruled": sum(1 for r in week if not r.get("ruling")),
            "listing_truncated": len(runs) >= 200,
        }
    if isinstance(budget, dict):
        report["budget"] = budget
    return report


def render(r: dict[str, Any]) -> str:
    days = r["hours"] / 24
    out = [
        f"# hookstack · cost & attention · last {days:.0f} days",
        "",
        f"_Generated {time.strftime('%Y-%m-%d %H:%M %Z', time.localtime(r['generated_at']))}. Measured figures are what was billed and delivered; counterfactuals are what the cost policy avoided, priced at this week's average paid call, and are estimates of a bill that did not happen._",
        "",
    ]

    j = r.get("judge")
    out += ["## The judge", ""]
    if not j:
        out.append("_Not read (no judge URL/token, or unreachable)._")
    else:
        fr = j["free_routes"]
        out += [
            f"- **Measured**: {j['judged']} verdicts, {j['paid']} paid ({j['paid_ratio_pct']}%), **{_money(j['cost'])}** · {_money(j['avg_paid'])} per paid verdict",
            "- **Free routes**: "
            + ", ".join(f"{k} {v}" for k, v in fr.items())
            + (
                f" · rule floor {j['rule_floor']} (a degradation, not a saving)"
                if j["rule_floor"]
                else ""
            ),
            f"- **Counterfactual**: {j['avoided_verdicts']} verdicts answered without a model call ≈ **{_money(j['avoided_cost'])} avoided**",
        ]
    a = (j or {}).get("attention")
    out += ["", "## Attention", ""]
    if not a:
        out.append("_Not read._")
    else:
        out += [
            f"- **Interruptions**: {a['interruptions']} across {a['conditions']} conditions · repeats {a['repeats']} ({_pct(a['repeats'], a['interruptions'])}) · likely flapping {a['likely_flapping']}",
            f"- **Kept from a person**: wake=no {a['wake_no']} · wake=yes {a['wake_yes']} → **{a['delivered_per_condition']} delivered cards per condition**",
            f"- **Worth, as ruled by people**: mattered {a['mattered']} · did not matter {a['did_not_matter']} · ruled {a['ruled']}"
            + (
                f" · quiet regrets {a['quiet_regrets']}"
                if a.get("quiet_regrets") not in (None, "")
                else ""
            ),
        ]

    inv = r.get("investigator")
    out += ["", "## The investigator", ""]
    if not inv:
        out.append("_Not read (no investigator URL/token, or unreachable)._")
    else:
        out += [
            f"- **Measured**: {inv['runs']} runs, **{_money(inv['cost'])}** · {_money(inv['avg_run'])} per paid run",
            f"- **Counterfactual**: {inv['answered_from_runbook']} re-fires answered from a runbook at $0 ≈ **{_money(inv['avoided_cost'])} avoided**",
            f"- **Worth**: useful {inv['ruled_useful']} · useless {inv['ruled_useless']} · **unruled {inv['unruled']}** — the half only a person can fill",
        ]
        if inv.get("listing_truncated"):
            out.append("- _The run listing was capped; counts above may be low._")
    b = r.get("budget")
    if isinstance(b, dict) and b.get("enabled"):
        out.append(
            f"- **Budget** ({b.get('window_hours')}h window): {_money(b.get('spent_usd'))} of {_money(b.get('budget_usd'))} spent, {_money(b.get('remaining_usd'))} left"
            + (
                f" · cache hit {float(b.get('cache_hit_ratio') or 0) * 100:.0f}%"
                if b.get("cache_hit_ratio") is not None
                else ""
            )
            + (" · **EXHAUSTED**" if b.get("exhausted") else "")
        )

    p = r.get("pipe")
    out += ["", "## The pipe", ""]
    if not p:
        out.append("_Not read (no pipe URL/token, or unreachable)._")
    else:
        out += [
            f"- **Measured** over the last {p['chains']} chains ({p['span_days']} days): {p['hops']} hops, **{_money(p['priced_cost'])}** priced on the returns that carry a cost ({p['unpriced_hops']} hops unpriced by design)",
            f"- **Incidents with more than one card**: {p['incidents_multi']}",
        ]
        top = [i for i in p["top_incidents"] if float(i.get("cost_usd") or 0) > 0]
        if top:
            out += [
                "",
                "| incident | cards | cost | example |",
                "| --- | ---: | ---: | --- |",
            ]
            out += [
                f"| {i.get('incident')} | {i.get('interruptions')} | {_money(i.get('cost_usd'))} | {str(i.get('example') or '')[:60]} |"
                for i in top
            ]

    total = (j or {}).get("cost", 0.0) + (inv or {}).get("cost", 0.0)
    avoided = (j or {}).get("avoided_cost", 0.0) + (inv or {}).get("avoided_cost", 0.0)
    out += [
        "",
        "## One line",
        "",
        f"**Billed {_money(total)}; the cost policy avoided ≈ {_money(avoided)} more.** "
        + (
            "Delivered cards per condition: " + str(a["delivered_per_condition"]) + "."
            if a and a.get("delivered_per_condition") is not None
            else ""
        ),
    ]
    return "\n".join(out).rstrip() + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--relay", default=os.environ.get("HOOKRELAY_URL", ""))
    ap.add_argument("--judge", default=os.environ.get("HOOKJUDGE_URL", ""))
    ap.add_argument("--probe", default=os.environ.get("HOOKPROBE_URL", ""))
    ap.add_argument("--hours", type=float, default=168.0)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    now = time.time()
    judge = (
        _get(
            f"{args.judge.rstrip('/')}/status?window_hours={int(args.hours)}",
            os.environ.get("HOOKJUDGE_READ_TOKEN", ""),
        )
        if args.judge
        else None
    )
    timeline = (
        _get(
            f"{args.relay.rstrip('/')}/timeline?limit=500",
            os.environ.get("HOOKRELAY_READ_TOKEN", ""),
        )
        if args.relay
        else None
    )
    budget = (
        _get(
            f"{args.probe.rstrip('/')}/v1/budget", os.environ.get("HOOKPROBE_TOKEN", "")
        )
        if args.probe
        else None
    )
    runs = (
        _get(
            f"{args.probe.rstrip('/')}/v1/runs?limit=200",
            os.environ.get("HOOKPROBE_TOKEN", ""),
        )
        if args.probe
        else None
    )
    if isinstance(runs, dict):
        runs = runs.get("runs")
    report = compute(
        judge if isinstance(judge, dict) else None,
        timeline if isinstance(timeline, dict) else None,
        budget if isinstance(budget, dict) else None,
        runs if isinstance(runs, list) else None,
        hours=args.hours,
        now=now,
    )
    sys.stdout.write(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if args.json
        else render(report)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
