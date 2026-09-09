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
    assert "Counterfactuals are what the cost policy avoided" in page
    assert "**Measured**: 320 verdicts, 150 paid (46.9%)" in page
    assert "166 verdicts answered without a model call" in page
    assert "| burst-1 | 3 |" in page and "solo:9" not in page, "only priced incidents make the table"
    assert "unruled 2" in page


def test_a_dollar_on_this_page_is_priced_and_never_called_billed() -> None:
    """The two shadow arms are what disproved the old label: the same model on
    the same 31 paid calls, costs 15% apart, because their per-1k constants
    differed and nothing else did. A figure two configurations disagree about by
    15% is not a bill, and this page is the one place it gets quoted.
    """
    page = cost_report.render(cost_report.compute(JUDGE, TIMELINE, None, RUNS, hours=168, now=NOW))
    # The counts and the dollars are separate bullets, because one of them is
    # read off a ledger and the other is arithmetic over an unconfirmed rate.
    assert "**Measured**: 320 verdicts, 150 paid (46.9%)" in page
    assert "**Priced**: **$0.0900**" in page
    assert "HOOKJUDGE_AI_PRICE_IN_PER_1K" in page, "a reader can check the constants it was priced from"
    assert "**Priced figures are dollars, and not a bill**" in page
    # The sentence that gets quoted out of context is the one that must not lie.
    assert "**Priced at $" in page
    assert "Billed" not in page, "no figure on this page was ever billed to anybody"
    assert "15% apart on identical traffic" in page


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


def test_the_work_metrics_answer_the_product_questions_over_one_window():
    """Every figure here was already recorded somewhere and had never been added
    up. The board answers "what is happening now"; these answer "how did the
    week go", which is a different question and belongs on a different page."""
    import time

    now = time.time()

    def item(state, **kw):
        base = {"state": state, "opened_at": now - 3600, "verified": False, "hands_on": False, "refires": 0}
        return {**base, **kw}

    items = [
        item("done", first_result_at=now - 3000, verified=True),
        item("done", first_result_at=now - 3400, verified=True, hands_on=True, refires=2, resumes=1),
        item("done", first_result_at=now - 2000),
        item("abandoned", retries=1),
        item("waiting_approval", hands_on=True),
        # Older than the window: counted by none of it.
        item("done", opened_at=now - 40 * 86400, verified=True),
    ]
    proposals = [
        {"created_at": now - 3600, "approved_at": now - 1800},
        {"created_at": now - 7200, "approved_at": now - 6900},
        {"created_at": now - 60},  # never answered: not a wait, not a zero
        {"created_at": now - 90 * 86400, "approved_at": now - 89 * 86400},  # outside the window
    ]
    m = cost_report.work_metrics({"items": items}, proposals, hours=168, now=now)

    assert m["opened"] == 5, "windowed by when the work opened, so a piece of work and its outcome land in one week"
    assert m["completed"] == 3 and m["completion_pct"] == 60.0
    assert m["verified_pct"] == 66.7, "two of the three completed"
    assert m["closed_unattended"] == 1 and m["closed_unattended_pct"] == 20.0, "hands_on does not count"
    assert m["ended_without_answer_pct"] == 20.0
    assert m["repeat_pct"] == 20.0
    assert m["first_result_p50_seconds"] == 600.0, "median, not mean"
    assert m["approval_wait_p50_seconds"] == 1050.0 and m["approvals_answered"] == 2
    assert m["resumed"] == 1 and m["resume_success_pct"] == 100.0
    assert m["handed_to_a_person"] == 1

    # Nothing interrupted is the common and good case, and it is not zero percent.
    quiet = cost_report.work_metrics({"items": [item("done", verified=True)]}, [], hours=168, now=now)
    assert quiet["resume_success_pct"] is None and quiet["approval_wait_p50_seconds"] is None

    # A window with no work says so rather than dividing by it.
    assert cost_report.work_metrics({"items": []}, [], hours=168, now=now) == {"opened": 0}
    assert cost_report.work_metrics(None, None, hours=168, now=now) is None


def test_the_work_section_is_absent_rather_than_zero_when_the_board_was_not_read():
    page = cost_report.render({"hours": 168, "generated_at": 0.0, "work": None})
    assert "## The work" not in page, "a service that was not read gets no section, not a page of zeros"
    page = cost_report.render({"hours": 168, "generated_at": 0.0, "work": {"opened": 0}})
    assert "_No work opened in this window._" in page


def test_a_quiet_week_reads_as_a_sentence_not_a_row_of_dashes():
    """The first real render on production printed "0 interrupted by a restart,
    —% of those finished anyway · 0 provider blips retried" — every number true
    and the line unreadable. A week where nothing went wrong should say so."""
    quiet = {
        "opened": 1,
        "completed": 1,
        "completion_pct": 100.0,
        "verified_pct": 0.0,
        "closed_unattended": 0,
        "closed_unattended_pct": 0.0,
        "ended_without_answer_pct": 0.0,
        "repeat_pct": 0.0,
        "first_result_p50_seconds": 30.0,
        "approval_wait_p50_seconds": None,
        "approvals_answered": 0,
        "resumed": 0,
        "resume_success_pct": None,
        "auto_retries": 0,
        "handed_to_a_person": 0,
    }
    page = cost_report.render({"hours": 168, "generated_at": 0.0, "work": quiet})
    assert "nothing was interrupted, no provider blip needed retrying" in page
    assert "—%" not in page

    busy = {**quiet, "resumed": 2, "resume_success_pct": 50.0, "auto_retries": 1, "handed_to_a_person": 3}
    page = cost_report.render({"hours": 168, "generated_at": 0.0, "work": busy})
    assert "2 interrupted by a restart, 50.0% of those finished anyway" in page
    assert "1 provider blip retried" in page, "one blip is not one blips"


def test_an_unreachable_node_is_printed_rather_than_omitted():
    """The one interesting decision in a cross-node overview.

    A node that could not be read has an UNKNOWN board. An overview that
    quietly drops it reports "nothing is blocked" on evidence it does not have,
    which is the same failure as the console that was up and healthy while
    twelve pieces of work sat abandoned on it for three weeks — only faster,
    and with more confidence behind it.
    """
    survey = cost_report.survey_nodes(
        [
            ("http://a:8088", {"name": "planner", "policy": {"bash_guard": "readonly"}}, {"counts": {"done": 3}}),
            ("http://b:8089", None, None),
        ]
    )
    assert survey["asked"] == 2 and survey["boards_read"] == 1 and survey["unreachable"] == 1

    page = cost_report.render({"hours": 168, "generated_at": 0.0, "nodes": survey})
    assert "http://b:8089" in page, "a node that could not be read still gets a line"
    assert "could not be read" in page
    assert "unknown rather than zero" in page
    assert "1 node could not be read" in page, "one node, not '1 node(s)'"


def test_a_node_whose_board_timed_out_is_not_a_node_with_no_work():
    """Two doors, tracked apart: identity answering proves nothing about the
    board, and a row showing an empty board would be an invention."""
    survey = cost_report.survey_nodes([("http://a:8088", {"name": "watcher"}, None)])
    assert survey["unreachable"] == 0, "the node answered; it is not unreachable"
    assert survey["boards_unread"] == 1 and survey["boards_read"] == 0
    page = cost_report.render({"hours": 168, "generated_at": 0.0, "nodes": survey})
    assert "identity answered" in page and "board did not" in page
    assert "Its work is unknown." in page


def test_the_per_node_summary_names_the_one_that_needs_a_person():
    """The point of the section: not "I can see every node" but "the node that
    needs me finds me", on a page a clock already delivers."""
    survey = cost_report.survey_nodes(
        [
            (
                "http://a:8088",
                {"name": "planner", "runtime": {"adapter": "claude"}, "policy": {"bash_guard": "readonly"}},
                {"counts": {"done": 18, "verified": 0, "executing": 0, "blocked": 0, "abandoned": 0}},
            ),
            (
                "http://b:8089",
                {"name": "watcher", "runtime": {"adapter": "codex"}, "policy": {"bash_guard": "readonly"}},
                {"counts": {"done": 64, "verified": 1, "executing": 1, "blocked": 2, "abandoned": 1}},
            ),
        ]
    )
    assert survey["blocked"] == 2 and survey["abandoned"] == 1

    page = cost_report.render({"hours": 168, "generated_at": 0.0, "nodes": survey})
    assert "**2 waiting on a person · 1 abandoned · 2 of 2 boards read.**" in page
    assert "could not be read" not in page, "nothing was missed, so nothing claims to have been"
    # The node with nothing outstanding says so in words; the one with work
    # outstanding is the only place bold survives, so the eye finds it.
    quiet_row = (
        "- **planner** · claude/readonly — 18 done, 0 verified · 0 in flight · nothing waiting · nothing abandoned"
    )
    assert quiet_row in page
    assert "**2 waiting on a person**" in page and "**1 abandoned**" in page
    # The posture is on the row because exactly one node on a real deployment is
    # allowed to write; the role sentence is not, because truncating it cuts it
    # mid-clause and reads worse than saying nothing.
    assert "codex/readonly" in page
    assert "failure rate" not in page, "blocked and abandoned already carry it; two figures can disagree"


def test_a_single_node_deployment_gets_no_per_node_section():
    """A page repeating six numbers that already have a home is a second place
    for them to be wrong in. With one probe the page is unchanged."""
    page = cost_report.render({"hours": 168, "generated_at": 0.0})
    assert "## The nodes" not in page
