"""scripts/cost_report.py does the arithmetic itself, and labels what it did not measure.

The governance page is the one document where an approximate figure is worse
than none, so the numbers come from the three ledgers and this pins them:
counterfactuals are counts times this week's average paid call and say so; a
service that was not read gets a sentence, not a zero.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "cost_report", Path(__file__).resolve().parents[2] / "scripts" / "cost_report.py"
)
cost_report = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(cost_report)

JUDGE = {
    "summary": {
        "judged": 320,
        "cost": 0.09,
        "paid_ratio_pct": 46.9,
        "routes": {
            "ai": {"count": 150, "cost": 0.09},
            "recovery": {"count": 122},
            "reuse": {"count": 44},
            "rule": {"count": 4},
        },
        "attention": {
            "interruptions": 320,
            "conditions": 18,
            "repeats": 302,
            "wake_yes": 11,
            "wake_no": 303,
            "likely_flapping": 7,
            "mattered": 0,
            "did_not_matter": 0,
            "ruled": 0,
        },
    }
}
NOW = 1_000_000.0
RUNS = [
    {"session_key": "probe:a:1", "status": "completed", "finished_at": NOW - 3600, "cost_usd": 0.5, "ruling": "useful"},
    {"session_key": "probe:a:2", "status": "completed", "finished_at": NOW - 7200, "cost_usd": 0.7, "ruling": ""},
    {
        "session_key": "probe:a:3",
        "status": "completed",
        "finished_at": NOW - 9000,
        "cost_usd": 0.0,
        "answered_from_runbook": True,
        "ruling": "",
    },
    {
        "session_key": "probe:a:old",
        "status": "completed",
        "finished_at": NOW - 10 * 86400,
        "cost_usd": 9.0,
        "ruling": "",
    },
]
TIMELINE = {
    "totals": {"chains": 3, "hops": 5, "cost_usd": 1.2, "unpriced_hops": 3},
    "chains": [
        {"chain": "1", "started_at": NOW - 2 * 86400},
        {"chain": "2", "started_at": NOW - 86400},
        {"chain": "3", "started_at": NOW - 100},
    ],
    "incidents": [
        {
            "incident": "burst-1",
            "interruptions": 3,
            "cost_usd": 1.2,
            "chains": ["1", "2", "3"],
            "example": "SES bounce",
        },
        {"incident": "solo:9", "interruptions": 1, "cost_usd": 0.0, "chains": ["9"]},
    ],
}


def test_counterfactuals_are_counts_times_this_weeks_average_paid_call() -> None:
    r = cost_report.compute(
        JUDGE,
        TIMELINE,
        {"enabled": True, "spent_usd": 4.4, "budget_usd": 20, "remaining_usd": 15.6, "window_hours": 24},
        RUNS,
        hours=168,
        now=NOW,
    )
    j = r["judge"]
    assert j["paid"] == 150 and j["cost"] == 0.09 and j["avg_paid"] == 0.0006
    assert j["free_routes"] == {"recovery": 122, "reuse": 44} and j["rule_floor"] == 4, (
        "the rule floor is a degradation, never counted as a saving"
    )
    assert j["avoided_verdicts"] == 166 and j["avoided_cost"] == round(166 * 0.0006, 4)
    assert j["attention"]["delivered_per_condition"] == round((320 - 303) / 18, 2)
    inv = r["investigator"]
    assert inv["runs"] == 3, "the ten-day-old run is outside the window"
    assert inv["cost"] == 1.2 and inv["avg_run"] == 0.6, (
        "average over PAID runs, so a $0 runbook answer does not dilute it"
    )
    assert inv["answered_from_runbook"] == 1 and inv["avoided_cost"] == 0.6
    assert inv["ruled_useful"] == 1 and inv["unruled"] == 2
    assert r["pipe"]["incidents_multi"] == 1 and r["pipe"]["span_days"] == 2.0


def test_a_service_not_read_is_a_sentence_not_a_zero() -> None:
    page = cost_report.render(cost_report.compute(None, None, None, None, hours=168, now=NOW))
    assert page.count("_Not read") >= 3
    assert "$0.0000 avoided" not in page.split("## One line")[0]


def test_the_page_states_what_is_measured_and_what_is_not() -> None:
    page = cost_report.render(cost_report.compute(JUDGE, TIMELINE, None, RUNS, hours=168, now=NOW))
    assert "counterfactuals are what the cost policy avoided" in page
    assert "**Measured**: 320 verdicts, 150 paid (46.9%)" in page
    assert "166 verdicts answered without a model call" in page
    assert "| burst-1 | 3 |" in page and "solo:9" not in page, "only priced incidents make the table"
    assert "unruled 2" in page


def test_a_capped_listing_only_counts_as_truncated_when_the_window_might_extend_past_it() -> None:
    old_tail = [
        {"session_key": f"probe:x:{i}", "status": "completed", "finished_at": NOW - 30 * 86400, "cost_usd": 0.1}
        for i in range(200)
    ]
    r = cost_report.compute(None, None, None, old_tail, hours=168, now=NOW)
    assert r["investigator"]["listing_truncated"] is False, (
        "200 rows, but the oldest is a month old: the week is fully covered"
    )
    fresh = [
        {"session_key": f"probe:y:{i}", "status": "completed", "finished_at": NOW - 60 * i, "cost_usd": 0.1}
        for i in range(200)
    ]
    assert cost_report.compute(None, None, None, fresh, hours=168, now=NOW)["investigator"]["listing_truncated"] is True


def test_arms_are_compared_on_the_alert_both_saw_and_the_quiet_cell_is_named() -> None:
    live = [
        {"correlation_id": "hr-1", "importance": "low", "wake_someone": "no", "summary": "disk 71%"},
        {"correlation_id": "hr-2", "importance": "high", "wake_someone": "yes", "summary": "payments down"},
        {
            "correlation_id": "hr-3",
            "importance": "medium",
            "wake_someone": "no",
            "summary": "queue lag",
            "is_recovery": True,
        },
        {"correlation_id": "", "importance": "low", "wake_someone": "no"},
    ]
    shadow = [
        {"correlation_id": "hr-1", "importance": "high", "wake_someone": "yes"},  # live quieter: the cell with teeth
        {"correlation_id": "hr-2", "importance": "high", "wake_someone": "no"},  # live louder
        {
            "correlation_id": "hr-3",
            "importance": "high",
            "wake_someone": "yes",
            "is_recovery": True,
        },  # recoveries excluded
        {"correlation_id": "hr-9", "importance": "low", "wake_someone": "no"},  # not seen by live: not compared
    ]
    out = cost_report.compare_arms(live, [("http://b", shadow), ("http://c", None)])
    b, c = out["arms"]
    assert (b["compared"], b["importance_differs"], b["live_quieter"], b["live_louder"]) == (2, 1, 1, 1)
    assert b["live_quieter_examples"] == ["disk 71%"]
    assert c["unavailable"] is True
    page = cost_report.render({**cost_report.compute(None, None, None, None, hours=168, now=NOW), "arms": out})
    assert "live quieter than the shadow: 1" in page and "disk 71%" in page and "`http://c`: _not read_" in page
