#!/usr/bin/env python3
"""The week's bill and the week's attention, as one page for whoever pays.

Deterministic on purpose. The family already keeps every number this needs —
the judge's routes and costs, the pipe's priced chains and incidents, the
investigator's runs, budget and rulings — and a governance report is the one
document where an approximate figure is worse than none. So this asks the
three read APIs and does the arithmetic itself; no model is paid to summarise
a ledger, and the same inputs always give the same page.

Three kinds of number are kept apart and labelled:

  measured       counts, shares and durations — verdicts served, runs, hops,
                 latency, who ruled what. Read off a ledger; no price table is
                 involved and none can be wrong.
  priced         dollars. A measured quantity times a price table, and on this
                 stack that table is NOT the gateway's: the judge multiplies
                 tokens by two constants an operator set
                 (HOOKJUDGE_AI_PRICE_IN_PER_1K and ..._OUT_PER_1K), and the
                 investigator passes on the agent CLI's own estimate for a
                 model the CLI is not the one billing. Real arithmetic over a
                 rate nobody has confirmed.
  counterfactual what the cost policy AVOIDED — free verdicts (recovery,
                 reuse, rule-reuse) and runbook answers, priced at this week's
                 average paid call. An estimate of a bill that did not happen,
                 and it says so.

The middle row used to be called "measured" and the shadow arms are what
disproved it: two judges, the same model, the same 31 paid calls, and costs 15%
apart because their price constants differed and nothing else did. Saying
"billed" of a figure two configurations disagree about by 15% is the failure
this page exists to avoid, so it now says what it actually did. Getting from
priced to billed needs the gateway's own price list, which nothing in this
repository has; the arithmetic is ready for it.

    HOOKRELAY_READ_TOKEN=... HOOKJUDGE_READ_TOKEN=... HOOKPROBE_TOKEN=... \\
      python3 scripts/cost_report.py --relay http://127.0.0.1:8100 \\
        --judge http://127.0.0.1:8200 --probe http://127.0.0.1:8088 [--hours 168] [--json]

A service that is not given, or cannot be read, gets a section that says so
rather than a zero that looks like a fact. Exit 0 with the page on stdout.

`--probe` is repeatable (and takes a comma-separated list, so the crontab's
`HOOKPROBE_URL` can carry several). The first node drives every section; give
more and the page grows a per-node summary naming the one that needs a person.
That is the deliberately small form of the PRD's cross-node Overview: a signal
on a clock rather than a page nobody opens, with the peer URLs in the
operator's shell rather than distributed to every node.
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
    req = urllib.request.Request(url, headers={"X-Read-Token": token, "Authorization": f"Bearer {token}"})
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


def _p50(values: list[float]) -> float | None:
    """Median, not mean. One approval that waited three days would move a mean
    far enough to say nothing true about the other nine."""
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def work_metrics(work: dict | None, proposals: list | None, *, hours: float, now: float) -> dict[str, Any] | None:
    """The PRD's product metrics, over the work this window opened.

    Every figure here was already recorded somewhere and had never been added
    up: the board answers "what is happening now" and these answer "how did the
    last week go", which is a different question and a different page.

    Windowed by when the work OPENED, so a piece of work and the outcome it
    reached are counted in the same week rather than split across two.
    """
    if not isinstance(work, dict) or not isinstance(work.get("items"), list):
        return None
    cutoff = now - hours * 3600
    items = [i for i in work["items"] if float(i.get("opened_at") or 0) >= cutoff]
    total = len(items)
    if not total:
        return {"opened": 0}
    done = [i for i in items if i.get("state") == "done"]
    ended_badly = [i for i in items if i.get("state") in ("needs_human", "abandoned")]
    verified = [i for i in done if i.get("verified")]
    resumed = [i for i in items if int(i.get("resumes") or 0)]
    first_results = [
        float(i["first_result_at"]) - float(i["opened_at"])
        for i in items
        if i.get("first_result_at") and i.get("opened_at")
    ]
    waits = [
        float(p["approved_at"]) - float(p["created_at"])
        for p in (proposals or [])
        if isinstance(p, dict) and p.get("approved_at") and p.get("created_at") and float(p["created_at"]) >= cutoff
    ]
    return {
        "opened": total,
        "completed": len(done),
        # Ratios the PRD names. Each is over the work opened in the window, so
        # they add up against one denominator a reader can check.
        "completion_pct": round(100 * len(done) / total, 1),
        "verified_pct": round(100 * len(verified) / len(done), 1) if done else None,
        "closed_unattended": sum(1 for i in done if i.get("verified") and not i.get("hands_on")),
        "closed_unattended_pct": round(
            100 * sum(1 for i in done if i.get("verified") and not i.get("hands_on")) / total, 1
        ),
        "ended_without_answer_pct": round(100 * len(ended_badly) / total, 1),
        "repeat_pct": round(100 * sum(1 for i in items if int(i.get("refires") or 0)) / total, 1),
        # Latencies, median: how long until the work was any use, and how long a
        # person took to answer the one question only a person can answer.
        "first_result_p50_seconds": _p50(first_results),
        "approval_wait_p50_seconds": _p50(waits),
        "approvals_answered": len(waits),
        # How the work survived. `resume_success_pct` is None when nothing was
        # interrupted, which is the common and good case — not zero.
        "resumed": len(resumed),
        "resume_success_pct": (
            round(100 * sum(1 for i in resumed if i.get("state") == "done") / len(resumed), 1) if resumed else None
        ),
        "auto_retries": sum(int(i.get("auto_retries") or 0) for i in items),
        "handed_to_a_person": sum(int(i.get("retries") or 0) for i in items),
    }


def compute(
    judge: dict | None,
    timeline: dict | None,
    budget: dict | None,
    runs: list | None,
    *,
    work: dict | None = None,
    proposals: list | None = None,
    nodes: dict | None = None,
    declines: dict | None = None,
    hours: float,
    now: float,
) -> dict[str, Any]:
    """Every figure the page prints, from the raw API bodies. Pure."""
    report: dict[str, Any] = {"hours": hours, "generated_at": now}
    if work is not None or proposals is not None:
        report["work"] = work_metrics(work, proposals, hours=hours, now=now)
    if nodes is not None:
        report["nodes"] = nodes

    if isinstance(judge, dict) and isinstance(judge.get("summary"), dict):
        s = judge["summary"]
        routes: dict[str, dict] = s.get("routes") or {}
        paid = int((routes.get("ai") or {}).get("count") or 0)
        judged = int(s.get("judged") or 0)
        cost = float(s.get("cost") or 0.0)
        avg_paid = cost / paid if paid else 0.0
        free_routes = {
            k: int(v.get("count") or 0) for k, v in routes.items() if k in ("recovery", "reuse", "rule-reuse")
        }
        floor = int((routes.get("rule") or {}).get("count") or 0)
        att = s.get("attention") or {}
        # The one condition behind most of the week's delivered cards, from the
        # recent listing. The 2026-08-12 note left the digest question open until
        # "repeated cards from one condition" hurt; this is that number, named,
        # so the decision can be taken from the page rather than from a grep.
        loud: dict[str, int] = {}
        for row in judge.get("recent") if isinstance(judge.get("recent"), list) else []:
            if isinstance(row, dict) and str(row.get("wake_someone") or "").lower() == "yes":
                loud[str(row.get("title") or "?")] = loud.get(str(row.get("title") or "?"), 0) + 1
        loudest = max(loud.items(), key=lambda kv: kv[1]) if loud else None
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
            # The golden gate's last verdict, as deploy.sh recorded it on the
            # judge's volume. None until a deploy has run the gate there.
            "eval_gate": _eval_gate(judge.get("eval_gate"), now=now),
            "attention": {
                "interruptions": interruptions,
                "conditions": conditions,
                "repeats": int(att.get("repeats") or 0),
                "wake_yes": int(att.get("wake_yes") or 0),
                "wake_no": wake_no,
                "loudest": {"title": loudest[0], "wake_yes": loudest[1]} if loudest else None,
                "likely_flapping": int(att.get("likely_flapping") or 0),
                "mattered": int(att.get("mattered") or 0),
                "did_not_matter": int(att.get("did_not_matter") or 0),
                "ruled": int(att.get("ruled") or 0),
                "quiet_regrets": att.get("quiet_regrets"),
                "delivered_per_condition": round((interruptions - wake_no) / conditions, 2) if conditions else None,
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
            "unpriced_hops": int((timeline.get("totals") or {}).get("unpriced_hops") or 0),
            "span_days": round((now - oldest) / 86400, 1),
            "incidents_multi": len(multi),
            "top_incidents": sorted(incidents, key=lambda i: -float(i.get("cost_usd") or 0))[:5],
        }

    if isinstance(runs, list):
        since = now - hours * 3600
        # Synthetic runs — drills, by-hand checks — are left out: real machinery on
        # unreal work, and one of them once counted here as a re-fire answered
        # from a runbook.
        week = [
            r
            for r in runs
            if float(r.get("finished_at") or 0) >= since and r.get("status") != "running" and not r.get("synthetic")
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
            # How often the posture had to refuse. Reported because the number
            # was recorded and unread, which is the failure this page exists to
            # avoid: a run refused twenty times is an agent being steered, and
            # it used to leave the same trace as one refused once.
            "guard_trips": sum(int(r.get("guard_trips") or 0) for r in week),
            "runs_refused": sum(1 for r in week if int(r.get("guard_trips") or 0) > 0),
            # Only a real risk when the listing hit its cap AND its oldest row is
            # still inside the window — then older in-window runs may exist unseen.
            "listing_truncated": len(runs) >= 200
            and min((float(r.get("finished_at") or now) for r in runs), default=now) >= since,
        }
    if isinstance(declines, dict):
        # What the decline list saved, at this week's average paid run — the
        # same counterfactual arithmetic as the runbook line, and labelled as
        # such. Kept three-valued: "no list" is not "the list matched nothing".
        avg_run = float((report.get("investigator") or {}).get("avg_run") or 0.0)
        declined = int(declines.get("declined") or 0)
        report["declines"] = {
            "configured": bool(declines.get("configured")),
            "patterns": int(declines.get("patterns") or 0),
            "declined": declined,
            "conditions": int(declines.get("conditions") or 0),
            "avoided_cost": round(declined * avg_run, 4),
        }
    if isinstance(budget, dict):
        report["budget"] = budget
    return report


def _eval_gate(row: Any, *, now: float) -> dict[str, Any] | None:
    """The recorded gate verdict with its age, or None when none was recorded."""
    if not isinstance(row, dict) or not row.get("verdict"):
        return None
    at = float(row.get("at") or 0)
    return {
        "verdict": str(row["verdict"]),
        "age_days": round((now - at) / 86400, 1) if at else None,
        "firing_cases": int(row.get("firing_cases") or 0),
        "recovery_cases": int(row.get("recovery_cases") or 0),
        "recovery_under_called": int(row.get("recovery_under_called") or 0),
        "missed": int(row.get("missed") or 0),
        "false_quiet": int(row.get("false_quiet") or 0),
        "thin": bool(row.get("thin")),
    }


def compare_arms(live_rows: list | None, shadows: list[tuple[str, list | None]]) -> dict[str, Any]:
    """The live judge against each shadow arm, joined on the alert they both saw.

    Not an accuracy number — nobody knows which arm is right — but the shape of
    their disagreement, and the one cell with teeth: the live judge said
    wake=no where a shadow said wake=yes. Those are the alerts a person never
    saw that another model would have shown them. Pure; the fetch is elsewhere.
    """
    out: dict[str, Any] = {"arms": []}
    if not isinstance(live_rows, list):
        out["unavailable"] = True
        return out
    live = {
        str(r.get("correlation_id") or ""): r for r in live_rows if r.get("correlation_id") and not r.get("is_recovery")
    }
    for url, rows in shadows:
        if not isinstance(rows, list):
            out["arms"].append({"url": url, "unavailable": True})
            continue
        compared = importance_differs = live_quieter = live_louder = 0
        quieter_examples: list[str] = []
        for r in rows:
            key = str(r.get("correlation_id") or "")
            base = live.get(key)
            if not base or r.get("is_recovery"):
                continue
            compared += 1
            if (base.get("importance") or "") != (r.get("importance") or ""):
                importance_differs += 1
            lw, sw = (
                str(base.get("wake_someone") or "").lower(),
                str(r.get("wake_someone") or "").lower(),
            )
            if lw == "no" and sw == "yes":
                live_quieter += 1
                if len(quieter_examples) < 3:
                    quieter_examples.append(str(base.get("summary") or base.get("title") or key)[:70])
            elif lw == "yes" and sw == "no":
                live_louder += 1
        out["arms"].append(
            {
                "url": url,
                "compared": compared,
                "importance_differs": importance_differs,
                "importance_differs_pct": round(100.0 * importance_differs / compared, 1) if compared else None,
                "live_quieter": live_quieter,
                "live_louder": live_louder,
                "live_quieter_examples": quieter_examples,
            }
        )
    return out


def survey_nodes(rows: list[tuple[str, dict | None, dict | None]]) -> dict[str, Any]:
    """One line per investigator, for the deployments that run several.

    A cross-node overview was parked twice as a PAGE and it was the right call
    both times: the pipe would have to understand a field it only carries, and a
    page nobody opens is worth nothing on a deployment nobody watches. What was
    wanted was never the view — it was the signal. Not "I can see every node"
    but "the one node that needs me finds me", and the delivery mechanism for
    that already exists: this page is rendered by a clock and posted through a
    door of the pipe like everything else.

    So the fan-out lives here, in a script the operator runs, holding the peer
    URLs in the operator's own shell. Not probe-to-probe peering, which spends
    N-squared credentials to reach a place one script already stands in.

    **An unreachable node is printed, never omitted.** This is the whole
    interesting part of the feature. A node that could not be read has an
    unknown board, and an overview that quietly drops it reports "nothing is
    blocked" on evidence it does not have — which is the same failure as the
    board that was up and healthy while twelve pieces of work sat abandoned on
    it for three weeks, only faster and with more confidence. The two doors are
    tracked apart for the same reason: a node whose identity answered and whose
    board timed out is not a node with an empty board.
    """
    nodes: list[dict[str, Any]] = []
    for url, agent, work in rows:
        counts = work.get("counts") if isinstance(work, dict) else None
        counts = counts if isinstance(counts, dict) else None
        if not isinstance(agent, dict) and counts is None:
            nodes.append({"url": url, "unreachable": True})
            continue
        runtime = agent.get("runtime") if isinstance(agent, dict) else None
        policy = agent.get("policy") if isinstance(agent, dict) else None
        nodes.append(
            {
                "url": url,
                # Falls back to the URL rather than to "hookprobe": every node
                # answered to that name before /v1/agent existed, and a report
                # naming three of them identically is the problem this solves.
                "name": (agent.get("name") if isinstance(agent, dict) else "") or url,
                "role": (agent.get("role") if isinstance(agent, dict) else "") or "",
                "runtime": (runtime or {}).get("adapter") or "",
                "guard": (policy or {}).get("bash_guard") or "",
                "counts": counts,
                "identity_read": isinstance(agent, dict),
                "board_read": counts is not None,
            }
        )
    read = [n for n in nodes if n.get("counts")]
    return {
        "nodes": nodes,
        "asked": len(nodes),
        "boards_read": len(read),
        "unreachable": sum(1 for n in nodes if n.get("unreachable")),
        "boards_unread": sum(1 for n in nodes if not n.get("unreachable") and not n.get("board_read")),
        "blocked": sum(int(n["counts"].get("blocked") or 0) for n in read),
        "abandoned": sum(int(n["counts"].get("abandoned") or 0) for n in read),
    }


def render(r: dict[str, Any]) -> str:
    days = r["hours"] / 24
    out = [
        f"# hookstack · cost & attention · last {days:.0f} days",
        "",
        f"_Generated {time.strftime('%Y-%m-%d %H:%M %Z', time.localtime(r['generated_at']))}. Measured figures are counts read off the ledgers. **Priced figures are dollars, and not a bill** — a measured quantity times a price table that is not the gateway's, so read them as a shape. Counterfactuals are what the cost policy avoided, priced at this week's average paid call, and are estimates of a bill that did not happen._",
        "",
    ]

    w = r.get("work")
    if w is not None:
        out += ["## The work", ""]
        if not w.get("opened"):
            out.append("_No work opened in this window._")
        else:

            def _dur(seconds: float | None) -> str:
                if seconds is None:
                    return "—"
                if seconds < 90:
                    return f"{seconds:.0f}s"
                if seconds < 5400:
                    return f"{seconds / 60:.0f}m"
                return f"{seconds / 3600:.1f}h"

            out += [
                f"- **Opened**: {w['opened']} pieces of work · **completed** {w['completed']} ({w['completion_pct']}%) "
                f"· **ended without an answer** {w['ended_without_answer_pct']}%",
                f"- **Verified**: {w['verified_pct'] if w['verified_pct'] is not None else '—'}% of completed work — "
                "a person's ruling, its own procedure exiting 0, or the condition ending",
                f"- **Closed with nobody stepping in**: {w['closed_unattended']} ({w['closed_unattended_pct']}%) — "
                "the north star: finished, verified, and it never had to stop and ask",
                f"- **Time to first useful result**: {_dur(w['first_result_p50_seconds'])} (median)",
                f"- **A person's approval took**: {_dur(w['approval_wait_p50_seconds'])} (median of "
                f"{w['approvals_answered']} answered)",
                f"- **Repeated conditions**: {w['repeat_pct']}% of work re-fired at least once",
                (
                    "- **Survived**: nothing was interrupted, no provider blip needed retrying, "
                    "and nothing was handed back to a person"
                    if not (w["resumed"] or w["auto_retries"] or w["handed_to_a_person"])
                    else (
                        f"- **Survived**: {w['resumed']} interrupted by a restart, "
                        f"{w['resume_success_pct'] if w['resume_success_pct'] is not None else '—'}% of those finished "
                        f"anyway · {w['auto_retries']} provider blip"
                        f"{'' if w['auto_retries'] == 1 else 's'} retried automatically · "
                        f"{w['handed_to_a_person']} handed back to a person"
                    )
                ),
                "",
            ]

    n = r.get("nodes")
    if n is not None:
        out += ["## The nodes", ""]
        lead = (
            f"**{n['blocked']} waiting on a person · {n['abandoned']} abandoned · "
            f"{n['boards_read']} of {n['asked']} boards read.**"
        )
        missed = n["unreachable"] + n["boards_unread"]
        if missed:
            lead += (
                f" {missed} node{'' if missed == 1 else 's'} could not be read, so that work is "
                "**unknown rather than zero**."
            )
        out += [lead, ""]
        for node in n["nodes"]:
            if node.get("unreachable"):
                out.append(f"- `{node['url']}` — **could not be read**. Not an idle node; an unknown one.")
                continue
            if not node.get("board_read"):
                out.append(
                    f"- **{node['name']}** · `{node['url']}` — identity answered, "
                    "**board did not**. Its work is unknown."
                )
                continue
            c = node["counts"]
            # The name and the posture, not the role. A role is a sentence
            # written for one node's own console ("turns a work signal into a
            # plan a person can approve; hands …") and truncating it to fit a
            # row cuts it mid-clause, which reads as a worse version of saying
            # nothing. The names here are already self-describing; the posture
            # is not derivable from one, and on this deployment exactly one node
            # is allowed to write. It stays in --json for anything that wants it.
            head = f"- **{node['name']}**"
            badge = "/".join(part for part in (node["runtime"], node["guard"]) if part)
            if badge:
                head += f" · {badge}"
            # No failure rate on this row on purpose: it is
            # (needs_human + abandoned) / items, so blocked and abandoned below
            # already carry it, and two figures that can contradict each other
            # are worse than one. The board and --json still report it.
            facts = [
                f"{c.get('done') or 0} done, {c.get('verified') or 0} verified",
                f"{c.get('executing') or 0} in flight",
            ]
            # Bolded only when non-zero: on a page delivered weekly into a chat,
            # the eye has to find the node that needs somebody without reading
            # every figure on every row.
            blocked = int(c.get("blocked") or 0)
            abandoned = int(c.get("abandoned") or 0)
            facts.append(f"**{blocked} waiting on a person**" if blocked else "nothing waiting")
            facts.append(f"**{abandoned} abandoned**" if abandoned else "nothing abandoned")
            out.append(head + " — " + " · ".join(facts))
        out.append("")

    j = r.get("judge")
    out += ["## The judge", ""]
    if not j:
        out.append("_Not read (no judge URL/token, or unreachable)._")
    else:
        fr = j["free_routes"]
        out += [
            f"- **Measured**: {j['judged']} verdicts, {j['paid']} paid ({j['paid_ratio_pct']}%)",
            f"- **Priced**: **{_money(j['cost'])}** · {_money(j['avg_paid'])} per paid verdict — tokens times "
            "this deployment's own `HOOKJUDGE_AI_PRICE_IN_PER_1K` / `..._OUT_PER_1K`",
            "- **Free routes**: "
            + ", ".join(f"{k} {v}" for k, v in fr.items())
            + (f" · rule floor {j['rule_floor']} (a degradation, not a saving)" if j["rule_floor"] else ""),
            f"- **Counterfactual**: {j['avoided_verdicts']} verdicts answered without a model call ≈ **{_money(j['avoided_cost'])} avoided**",
        ]
        g = j.get("eval_gate")
        if not g:
            out.append("- **Golden gate**: no recorded run on this host — a deploy records one")
        else:
            age = "age unknown" if g["age_days"] is None else f"{g['age_days']} days ago"
            out.append(
                f"- **Golden gate at the last deploy**: **{g['verdict']}** {age} · "
                f"{g['firing_cases']} firing rows, {g['recovery_cases']} recovery rows "
                f"({g['recovery_under_called']} under-called)"
                + (f" · missed {g['missed']} · false quiet {g['false_quiet']}" if g["verdict"] != "green" else "")
                + (" — **thin**: too few firing rows for a green to mean much" if g["thin"] else "")
            )
    a = (j or {}).get("attention")
    out += ["", "## Attention", ""]
    if not a:
        out.append("_Not read._")
    else:
        out += [
            f"- **Interruptions**: {a['interruptions']} across {a['conditions']} conditions · repeats {a['repeats']} ({_pct(a['repeats'], a['interruptions'])}) · likely flapping {a['likely_flapping']}",
            f"- **Kept from a person**: wake=no {a['wake_no']} · wake=yes {a['wake_yes']} → **{a['delivered_per_condition']} delivered cards per condition**",
        ]
        if a.get("loudest"):
            loud = a["loudest"]
            out.append(
                f"- **Loudest condition**: {loud['wake_yes']} of the {a['wake_yes']} wake=yes cards "
                f"({_pct(loud['wake_yes'], a['wake_yes'])}) came from one condition — {loud['title']}"
                + (
                    "; one condition is most of the week's cards, which is the number the 2026-08-12 digest decision waits on"
                    if a["wake_yes"] and loud["wake_yes"] * 2 >= a["wake_yes"]
                    else ""
                )
            )
        out += [
            f"- **Worth, as ruled by people**: mattered {a['mattered']} · did not matter {a['did_not_matter']} · ruled {a['ruled']}"
            + (f" · quiet regrets {a['quiet_regrets']}" if a.get("quiet_regrets") not in (None, "") else ""),
        ]

    inv = r.get("investigator")
    out += ["", "## The investigator", ""]
    if not inv:
        out.append("_Not read (no investigator URL/token, or unreachable)._")
    else:
        out += [
            f"- **Measured**: {inv['runs']} runs",
            f"- **Priced**: **{_money(inv['cost'])}** · {_money(inv['avg_run'])} per paid run — the agent CLI's own "
            "estimate at its own table, for a model it is not the one billing",
            f"- **Counterfactual**: {inv['answered_from_runbook']} re-fires answered from a runbook at $0 ≈ **{_money(inv['avoided_cost'])} avoided**",
            f"- **Worth**: useful {inv['ruled_useful']} · useless {inv['ruled_useless']} · **unruled {inv['unruled']}** — the half only a person can fill",
            (
                f"- **The posture refused** {inv['guard_trips']} call"
                f"{'' if inv['guard_trips'] == 1 else 's'} across {inv['runs_refused']} run"
                f"{'' if inv['runs_refused'] == 1 else 's'} — a run refused repeatedly is an agent being "
                "steered, not one narrowing a query"
                if inv.get("guard_trips")
                else "- **The posture refused nothing** this window"
            ),
        ]
        if inv.get("listing_truncated"):
            out.append("- _The run listing was capped; counts above may be low._")
    d = r.get("declines")
    if inv and not d:
        out.append("- _Declines at the door: not read (the node predates `/v1/declines`, or it was unreachable)._")
    elif d and not d["configured"]:
        out.append("- **The door declines nothing by list** — no `HOOKPROBE_DECLINE_PATTERNS` on this node")
    elif d and not d["declined"]:
        out.append(
            f"- **Declined at the door**: nothing this window — the list has {d['patterns']} pattern"
            f"{'' if d['patterns'] == 1 else 's'} and none matched"
        )
    elif d:
        out.append(
            f"- **Declined at the door**: {d['declined']} event{'' if d['declined'] == 1 else 's'} across "
            f"{d['conditions']} condition{'' if d['conditions'] == 1 else 's'} ≈ **{_money(d['avoided_cost'])} avoided** "
            "(each priced at this week's average paid run)"
        )
    b = r.get("budget")
    if isinstance(b, dict) and b.get("enabled"):
        out.append(
            f"- **Budget** ({b.get('window_hours')}h window): {_money(b.get('spent_usd'))} of {_money(b.get('budget_usd'))} spent, "
            + (
                # A ceiling on a runtime that reports no money cannot trip, and
                # the old line said "$1.00 left" on a node whose spend nothing
                # could see. Headroom is only printed where it was measured.
                "**and the ceiling cannot bind** — every turn in this window was unpriced, so the spend above is not a floor, it is a blank"
                if b.get("spend_visibility") == "blind"
                else f"{_money(b.get('remaining_usd'))} left"
                + (" (a floor: some turns went unpriced)" if b.get("spend_visibility") == "floor" else "")
            )
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

    arms = r.get("arms")
    if arms is not None:
        out += ["", "## Shadow arms", ""]
        if arms.get("unavailable"):
            out.append("_The live judge's rows could not be read; no comparison._")
        for a2 in arms.get("arms") or []:
            if a2.get("unavailable"):
                out.append(f"- `{a2['url']}`: _not read_")
                continue
            out.append(
                f"- `{a2['url']}`: {a2['compared']} alerts both judged · importance differs on {a2['importance_differs']} ({a2['importance_differs_pct']}%) · "
                f"**live quieter than the shadow: {a2['live_quieter']}** (would have woken somebody) · live louder: {a2['live_louder']}"
            )
            for ex in a2.get("live_quieter_examples") or []:
                out.append(f"  - {ex}")
        out.append("")
        out.append(
            "_Disagreement is not error — nobody knows which arm is right — but the 'live quieter' cell is the one to read first._"
        )

    total = (j or {}).get("cost", 0.0) + (inv or {}).get("cost", 0.0)
    avoided = (j or {}).get("avoided_cost", 0.0) + (inv or {}).get("avoided_cost", 0.0)
    out += [
        "",
        "## One line",
        "",
        f"**Priced at {_money(total)}; the cost policy avoided ≈ {_money(avoided)} more.** "
        "Neither is a bill: both are measured quantities times price tables nobody has checked against "
        "the gateway, and two such tables were once 15% apart on identical traffic. "
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
    ap.add_argument(
        "--probe",
        action="append",
        default=[],
        help=(
            "an investigator's URL; repeatable, and a comma-separated list is accepted so the "
            "crontab can pass HOOKPROBE_URL. The FIRST is the deployment's primary and drives "
            "every section below; give more than one and the page grows a per-node summary."
        ),
    )
    ap.add_argument(
        "--shadow-judge",
        action="append",
        default=[],
        help="a shadow arm's URL; repeatable",
    )
    ap.add_argument("--hours", type=float, default=168.0)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    now = time.time()
    # One flag, three ways to spell a list, because the caller that matters most
    # is a crontab line that already exports HOOKPROBE_URL.
    given = list(args.probe) or [os.environ.get("HOOKPROBE_URL", "")]
    probes = [part.strip() for item in given for part in str(item).split(",") if part.strip()]
    # The first node stays the primary and every existing section reads it alone,
    # so the page a one-probe deployment gets is unchanged, character for
    # character. A second copy of the same six numbers under a new heading is
    # exactly the "second place to be wrong" this report already refuses.
    primary = probes[0] if probes else ""
    judge = (
        _get(
            f"{args.judge.rstrip('/')}/status?window_hours={int(args.hours)}&limit=500",
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
    budget = _get(f"{primary.rstrip('/')}/v1/budget", os.environ.get("HOOKPROBE_TOKEN", "")) if primary else None
    runs = (
        _get(
            f"{primary.rstrip('/')}/v1/runs?limit=200",
            os.environ.get("HOOKPROBE_TOKEN", ""),
        )
        if primary
        else None
    )
    if isinstance(runs, dict):
        runs = runs.get("runs")
    # The board's own derivation, read rather than recomputed: one place decides
    # what a piece of work is and what state it is in, and this page reports it.
    probe_token = os.environ.get("HOOKPROBE_TOKEN", "")
    work = _get(f"{primary.rstrip('/')}/v1/work?limit=500", probe_token) if primary else None
    declines = _get(f"{primary.rstrip('/')}/v1/declines?hours={int(args.hours)}", probe_token) if primary else None
    proposals = _get(f"{primary.rstrip('/')}/v1/remediations", probe_token) if primary else None
    if isinstance(proposals, dict):
        proposals = proposals.get("proposals")
    # Only when there IS more than one node. The section answers "which of my
    # nodes needs me", and a deployment with one node has already been told.
    nodes = None
    if len(probes) > 1:
        nodes = survey_nodes(
            [
                (
                    url,
                    _get(f"{url.rstrip('/')}/v1/agent", probe_token),
                    _get(f"{url.rstrip('/')}/v1/work?limit=500", probe_token),
                )
                for url in probes
            ]
        )
    arms = None
    if args.judge and args.shadow_judge:
        jt = os.environ.get("HOOKJUDGE_READ_TOKEN", "")
        live_rows = (judge or {}).get("recent") if isinstance(judge, dict) else None
        shadow_rows = []
        for url in args.shadow_judge:
            body = _get(f"{url.rstrip('/')}/status?window_hours={int(args.hours)}&limit=500", jt)
            shadow_rows.append((url, (body or {}).get("recent") if isinstance(body, dict) else None))
        arms = compare_arms(live_rows, shadow_rows)
    report = compute(
        judge if isinstance(judge, dict) else None,
        timeline if isinstance(timeline, dict) else None,
        budget if isinstance(budget, dict) else None,
        runs if isinstance(runs, list) else None,
        work=work if isinstance(work, dict) else None,
        proposals=proposals if isinstance(proposals, list) else None,
        nodes=nodes,
        declines=declines if isinstance(declines, dict) else None,
        hours=args.hours,
        now=now,
    )
    if arms is not None:
        report["arms"] = arms
    sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n" if args.json else render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
