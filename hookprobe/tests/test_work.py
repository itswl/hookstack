"""One piece of work is one row, however many runs it took.

The board answers "what is happening and what is blocked". Everything it says
is derived (hookprobe/work.py) from records that already existed, so these
tests are about the derivation: what groups together, what blocks, what counts
as verified, and what the north-star number is allowed to include.
"""

from __future__ import annotations

import json
import time
from typing import Any

from fastapi.testclient import TestClient

from hookprobe import work
from hookprobe.app import create_app
from hookprobe.runs import COMPLETED, FAILED, RUNNING, Run, RunStore
from hookprobe.service import RunService
from tests.helpers import FakeEngine, make_settings

TOKEN = "secret-token"


def _run(key: str, *, status: str = COMPLETED, cost: float = 0.1, meta: dict[str, Any] | None = None, **kw) -> Run:
    run = Run(session_key=key, run_id=key, status=status, text="report", **kw)
    run.meta = {"title": "disk 94% on node-3", "source": "ww", "kind": "alert", **(meta or {})}
    run.created_at = kw.get("created_at") or time.time() - 60
    run.finished_at = None if status == RUNNING else run.created_at + 30
    run.turns = [{"cost_usd": cost, "message": "m"}]
    return run


def test_a_refire_a_followup_and_a_handoff_are_one_item_not_four() -> None:
    """The three ways a work item outgrows a run, asserted together.

    A re-fire and a chat follow-up add turns to the run that is already the
    same work; a handoff makes a SECOND run on another node carry the same
    work id. A board that counted runs would report this as four pieces of
    work, three of them phantom.
    """
    plan = _run("probe:watch:7", meta={"work_id": "hr-7", "refires": 2, "follow_ups": ["om_a", "om_b"]})
    plan.turns = [{"cost_usd": 0.4}, {"cost_usd": 0.05}, {"cost_usd": 0.05}]
    done = _run("probe:plan-notify:9", meta={"work_id": "hr-7"}, cost=0.6)
    (item,) = work.resolve([plan, done])
    assert item.work_id == "hr-7"
    assert item.sessions == ["probe:watch:7", "probe:plan-notify:9"], "oldest first: the work opened with the plan"
    assert item.turns == 4 and round(item.cost_usd, 2) == 1.10, "the item's bill is every run's"
    assert item.refires == 2 and item.follow_ups == 2
    assert item.hands_on, "a person had to ask twice mid-flight"


def test_runs_nobody_stitched_stay_separate() -> None:
    items = work.resolve([_run("probe:ww:1"), _run("probe:ww:2")])
    assert {i.work_id for i in items} == {"probe:ww:1", "probe:ww:2"}, "no work id: the session key is the identity"


def test_an_open_proposal_blocks_the_work_and_names_the_command() -> None:
    run = _run("probe:ww:1")
    proposals = [
        {
            "id": "abc0123456",
            "session_key": "probe:ww:1",
            "status": "proposed",
            "created_at": time.time(),
            "steps": [{"command": "systemctl restart kubelet", "risk": "high", "action": "restart"}],
        }
    ]
    (item,) = work.resolve([run], proposals=proposals)
    assert item.state == work.WAITING_APPROVAL, "nothing runs until somebody presses"
    approve = next(o for o in item.open if o["kind"] == "approve")
    assert approve["text"] == "systemctl restart kubelet" and approve["ref"] == "abc0123456"
    assert item.hands_on and not item.verified
    assert {a["kind"] for a in item.artifacts} == {"report", "procedure"}


def test_a_procedure_that_ran_clean_verifies_the_work_but_does_not_close_it() -> None:
    """Every step exit 0 is the one verification this loop makes without a
    person. Whether the condition actually cleared is the next signal's answer,
    so the item waits in `verifying` rather than claiming to be done."""
    steps = [{"command": "a"}, {"command": "b"}]
    applied = {
        "id": "a" * 10,
        "session_key": "probe:ww:1",
        "status": "applied",
        "steps": steps,
        "results": [{"exit": 0}, {"exit": 0}],
    }
    (item,) = work.resolve([_run("probe:ww:1")], proposals=[applied])
    assert item.state == work.VERIFYING and item.verified and item.verified_by == "remediation"

    half = {**applied, "results": [{"exit": 0}, {"exit": 1}]}
    (item,) = work.resolve([_run("probe:ww:1")], proposals=[half])
    assert not item.verified, "a step that failed is not a verification"

    cut = {**applied, "interrupted": {"ran": ["a"], "not_run": ["b"]}}
    (item,) = work.resolve([_run("probe:ww:1")], proposals=[cut])
    assert not item.verified, "a procedure cut off mid-run is exactly what this must not pass"


def test_a_ruling_verifies_and_closes_it() -> None:
    run = _run("probe:ww:1")
    run.ruling, run.ruled_at, run.ruled_by = "useful", time.time(), "operator"
    (item,) = work.resolve([run])
    assert item.state == work.DONE and item.verified and item.verified_by == "ruling"
    assert not any(o["kind"] == "ruling" for o in item.open), "ruled: nothing owed"
    assert not item.hands_on, "a closing ruling is not an intervention"


def test_a_failure_outranks_a_stale_success() -> None:
    ok = _run("probe:ww:1", meta={"work_id": "hr-1"})
    later = _run("probe:ww:2", status=FAILED, meta={"work_id": "hr-1"}, created_at=time.time() - 10)
    (item,) = work.resolve([ok, later])
    assert item.state == work.NEEDS_HUMAN, "the last thing that happened is what a person answers for"


def test_a_running_turn_outranks_everything() -> None:
    running = _run("probe:ww:1", status=RUNNING)
    proposals = [{"id": "b" * 10, "session_key": "probe:ww:1", "status": "proposed", "steps": [{"command": "x"}]}]
    (item,) = work.resolve([running], proposals=proposals)
    assert item.state == work.EXECUTING


def test_a_memory_suggestion_waits_on_a_person_without_blocking_the_work() -> None:
    """The report is delivered and the work is finished; the line is an offer
    for the NEXT run. It belongs on the approvals page, which answers the wider
    question, and not in the blocked column, which would be false."""
    run = _run("probe:ww:1")
    run.ruling = "useful"
    rows = [{"id": "s1", "session_key": "probe:ww:1", "status": "open", "line": "node-3 /var fills weekly"}]
    (item,) = work.resolve([run], suggestions=rows)
    assert item.state == work.DONE
    assert [o["kind"] for o in item.open] == ["memory"] and item.open[0]["text"].startswith("node-3")
    assert work.counts([item])["open_items"] == 1, "it waits on a person even though it blocks nothing"


def test_the_counts_are_the_header_line_and_the_north_star_is_strict() -> None:
    clean = _run("probe:ww:1", meta={"work_id": "w1"})
    clean.ruling = "useful"
    needed_a_person = _run("probe:ww:2", meta={"work_id": "w2", "follow_ups": ["om_1"]})
    needed_a_person.ruling = "useful"
    unruled = _run("probe:ww:3", meta={"work_id": "w3"})
    broken = _run("probe:ww:4", status=FAILED, meta={"work_id": "w4"})
    counts = work.counts(work.resolve([clean, needed_a_person, unruled, broken]))
    assert counts["done"] == 3 and counts["needs_human"] == 1 and counts["blocked"] == 1
    assert counts["verified"] == 2
    assert counts["closed_unattended"] == 1, "verified but hands-on does not count; unverified does not count"
    assert counts["unruled"] == 2, "two runs never got a verdict"
    assert counts["open_items"] == 0, "a ruling is feedback, not something a person is asked to do"


def _client(tmp_path, **overrides):
    settings = make_settings(tmp_path, token=TOKEN, **overrides)
    service = RunService(settings, FakeEngine(), RunStore(tmp_path / "results"))
    return TestClient(create_app(settings, service)), service, settings


def _wait(client: TestClient, key: str) -> None:
    end = time.time() + 3
    while time.time() < end:
        r = client.get(f"/v1/runs/{key}", headers={"Authorization": f"Bearer {TOKEN}"})
        if r.status_code == 200 and r.json().get("status") in ("completed", "failed"):
            return
        time.sleep(0.02)
    raise AssertionError("run did not finish")


def test_the_door_takes_the_work_id_an_upstream_node_stated_over_the_pipes_own() -> None:
    """A handoff says which work it belongs to; the pipe's correlation is only
    the fallback. Getting this order wrong would split a plan from its work."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        client, service, _ = _client(Path(tmp), escalate_levels=frozenset({"high"}))
        event = {
            "source": "to-work",
            "title": "do the plan",
            "body": "…",
            "level": "high",
            "event_id": 51,
            "fields": {"work_id": "hr-7", "kind": "task"},
        }
        r = client.post("/hooks/event", json=event, headers={"X-Hook-Correlation-Id": "hr-51"})
        key = r.json()["sessionKey"]
        _wait(client, key)
        assert service.get(key).meta["work_id"] == "hr-7"
        assert service.get(key).meta["kind"] == "task"

        # Nothing stated: the pipe's own handle for this hop's chain.
        event = {"source": "ww", "title": "disk", "body": "…", "level": "high", "event_id": 52, "fields": {}}
        r = client.post("/hooks/event", json=event, headers={"X-Hook-Correlation-Id": "hr-40"})
        key = r.json()["sessionKey"]
        _wait(client, key)
        assert service.get(key).meta["work_id"] == "hr-40"

        # Neither: the run is its own work.
        event = {"source": "ww", "title": "disk again", "body": "…", "level": "high", "event_id": 53, "fields": {}}
        key = client.post("/hooks/event", json=event).json()["sessionKey"]
        _wait(client, key)
        assert service.get(key).meta["work_id"] == key

        board = client.get("/v1/work", headers={"Authorization": f"Bearer {TOKEN}"}).json()
        assert {i["work_id"] for i in board["items"]} == {"hr-7", "hr-40", key}
        assert board["counts"]["done"] == 3

        card = client.get("/v1/agent", headers={"Authorization": f"Bearer {TOKEN}"}).json()
        assert card["name"] == "hookprobe" and card["runtime"]["adapter"] == "claude-code"
        assert "budget_usd" in card["policy"] and card["health"]["active_runs"] == 0
        assert "token" not in json.dumps(card).lower(), "an agent card carries no secrets"
