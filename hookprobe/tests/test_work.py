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

from hookprobe import remediation, work
from hookprobe.app import create_app
from hookprobe.runs import COMPLETED, FAILED, RUNNING, Run, RunStore
from hookprobe.service import RunService
from tests.helpers import FakeEngine, make_settings, read_hash

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


def test_a_procedure_that_ran_clean_waits_for_the_condition_to_answer() -> None:
    """Every step exit 0 is where the commands' story ends, not the work's.
    Whether the condition actually cleared is the next signal's answer, so the
    item waits in `verifying` — unverified, not failed — until it comes; a
    stamp from the recovery door verifies it, and only then."""
    steps = [{"command": "a"}, {"command": "b"}]
    applied = {
        "id": "a" * 10,
        "session_key": "probe:ww:1",
        # From the writer, never typed here. Typed, it said "applied" for
        # months — a status remediation has never written — so this test
        # passed on a row that cannot exist while the branch it covers was
        # dead. See test_remediation's end-to-end twin.
        "status": remediation.EXECUTED,
        "steps": steps,
        "results": [{"exit": 0}, {"exit": 0}],
        "executed_at": time.time(),
        "verifying_until": time.time() + 3600,
    }
    (item,) = work.resolve([_run("probe:ww:1")], proposals=[applied])
    assert item.state == work.VERIFYING and not item.verified and item.awaiting_evidence
    assert any("verifying" in a["name"] for a in item.artifacts if a["kind"] == "procedure")

    held = {**applied, "held": True, "held_by": "recovery", "approved_by": "ou_2"}
    (item,) = work.resolve([_run("probe:ww:1")], proposals=[held])
    assert item.state == work.DONE and item.verified and item.verified_by == "remediation"
    assert any("held · approved by ou_2" in a["name"] for a in item.artifacts if a["kind"] == "procedure")

    did_not = {**applied, "held": False, "held_by": "refire"}
    (item,) = work.resolve([_run("probe:ww:1")], proposals=[did_not])
    assert not item.verified and not item.awaiting_evidence and item.state == work.DONE

    half = {**applied, "results": [{"exit": 0}, {"exit": 1}]}
    (item,) = work.resolve([_run("probe:ww:1")], proposals=[half])
    assert not item.verified, "a step that failed is not a verification"

    cut = {**applied, "interrupted": {"ran": ["a"], "not_run": ["b"]}}
    (item,) = work.resolve([_run("probe:ww:1")], proposals=[cut])
    assert not item.verified, "a procedure cut off mid-run is exactly what this must not pass"


def test_a_procedure_that_ran_clean_and_a_condition_that_ended_is_done() -> None:
    """The one sequence the loop exists for: the steps ran, then the alert
    resolved. The item was waiting in `verifying` for a ruling nobody was going
    to give, because this branch never looked at the recovery the door had
    already recorded on the run."""
    applied = {
        "id": "b" * 10,
        "session_key": "probe:ww:1",
        "status": remediation.EXECUTED,
        "steps": [{"command": "a"}],
        "results": [{"exit": 0}],
    }
    run = _run("probe:ww:1", meta={"recovered_at": time.time()})
    (item,) = work.resolve([run], proposals=[applied])
    assert item.verified and item.verified_by == "remediation", "the stronger witness keeps the credit"
    assert item.recovered
    assert item.state == work.DONE, "steps ran and the condition ended: nothing is owed"


def test_a_ruling_verifies_and_closes_it() -> None:
    run = _run("probe:ww:1")
    run.ruling, run.ruled_at, run.ruled_by = "useful", time.time(), "operator"
    (item,) = work.resolve([run])
    assert item.state == work.DONE and item.verified and item.verified_by == "ruling"
    assert not any(o["kind"] == "ruling" for o in item.open), "ruled: nothing owed"
    assert not item.hands_on, "a closing ruling is not an intervention"


def test_a_patrols_ruling_settles_the_debt_but_verifies_nothing() -> None:
    """The run-rulings patrol files `useful` under a `patrol:` prefix. On the
    production deployment all 98 rulings ever filed on a run were its, and this
    board counted each one as a person's verification — the north star graded
    by the model it was grading."""
    run = _run("probe:ww:1")
    run.ruling, run.ruled_at, run.ruled_by = "useful", time.time(), "patrol:run-rulings:2"
    (item,) = work.resolve([run])
    assert item.state == work.DONE and not item.verified and item.verified_by == ""
    assert not any(o["kind"] == "ruling" for o in item.open), "the patrol answered; nobody is asked twice"
    header = work.counts([item])
    assert header["verified"] == 0 and header["closed_unattended"] == 0


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
        assert card["name"] == "hookprobe" and card["runtime"]["adapter"] == "claude"
        assert "budget_usd" in card["policy"] and card["health"]["active_runs"] == 0
        assert "token" not in json.dumps(card).lower(), "an agent card carries no secrets"


def test_a_failure_nobody_came_back_to_leaves_the_blocked_count(tmp_path=None) -> None:
    """`blocked` has to mean "a person could act on this now". Production's
    board opened with twelve items in `needs a human`, two worth acting on and
    the oldest three weeks old — a number mixing "look at this today" with
    "nobody ever did" is the same mistake as counting unruled reports as debts."""
    fresh = _run("probe:ww:1", status=FAILED, meta={"work_id": "w1"})
    fresh.finished_at = time.time() - 3600
    old = _run("probe:ww:2", status=FAILED, meta={"work_id": "w2"})
    old.finished_at = time.time() - 21 * 86400

    items = {i.work_id: i for i in work.resolve([fresh, old])}
    assert items["w1"].state == work.NEEDS_HUMAN
    assert items["w2"].state == work.ABANDONED

    counts = work.counts(list(items.values()))
    assert counts["needs_human"] == 1 and counts["abandoned"] == 1
    assert counts["blocked"] == 1, "abandoned work is a count, not a queue"


def test_a_proposal_past_its_window_cannot_be_approved_and_stops_asking(tmp_path) -> None:
    """A procedure is commands chosen from evidence gathered at one moment.
    Approving it a week later runs a decision about a system that has moved,
    and the person pressing cannot see that. The card's button expires after a
    day; the console had no equivalent, so a three-week-old proposal was one
    click from a shell."""
    import time as _time

    import pytest

    from hookprobe import remediation

    workdir = tmp_path
    fresh_id = remediation.propose(workdir, "probe:ww:1", [{"command": "echo ok", "action": "look", "risk": "low"}])
    old_id = remediation.propose(workdir, "probe:ww:2", [{"command": "echo ok", "action": "look", "risk": "low"}])
    old = remediation.load(workdir, old_id)
    old["created_at"] = _time.time() - 3 * 86400
    remediation.save(workdir, old)

    allow = tmp_path / "allow.txt"
    allow.write_text("echo ok\n", encoding="utf-8")  # patterns are full-match regexes, not globs

    # The gate refuses, and says why in words an operator can act on.
    with pytest.raises(ValueError, match="past the 24h window"):
        remediation.approve(workdir, old_id, allowlist=allow, read_hash=read_hash(workdir, old_id))
    assert remediation.load(workdir, old_id)["status"] == "proposed", "refused, not silently resolved"
    assert (
        remediation.approve(workdir, fresh_id, allowlist=allow, read_hash=read_hash(workdir, fresh_id))["status"]
        == "running"
    )

    # And the board stops offering it, so `blocked` keeps meaning "somebody can
    # clear this now".
    runs = [_run("probe:ww:1", meta={"work_id": "w1"}), _run("probe:ww:2", meta={"work_id": "w2"})]
    items = {i.work_id: i for i in work.resolve(runs, proposals=[remediation.load(workdir, old_id)])}
    assert items["w2"].state == work.DONE
    assert not [o for o in items["w2"].open if o["kind"] == "approve"]
    assert any("expired unapproved" in a["name"] for a in items["w2"].artifacts), "still visible, just not offered"


def test_the_failure_rate_counts_work_not_runs(tmp_path=None) -> None:
    """A run that failed and was retried into an answer is not a piece of work
    that failed. Abandoned work is: nobody came, so it ended without one."""
    ok = _run("probe:ww:1", meta={"work_id": "w1"})
    retried = _run("probe:ww:2", meta={"work_id": "w2", "auto_retries": 1})
    lost = _run("probe:ww:3", status=FAILED, meta={"work_id": "w3"})
    gone = _run("probe:ww:4", status=FAILED, meta={"work_id": "w4"})
    gone.finished_at = time.time() - 30 * 86400

    counts = work.counts(work.resolve([ok, retried, lost, gone]))
    assert counts["failure_rate"] == 0.5, "two of four ended without an answer"
    assert work.counts([])["failure_rate"] is None, "no work, no rate — not zero"


def test_the_board_says_how_long_until_an_answer_and_how_many_signals_it_took() -> None:
    """The two numbers an on-call reads first, and neither existed. The board
    led with `closed_unattended` and a failure rate — both about outcome, and
    silent on the two questions a person actually opens it with: how long until
    it was any use, and how much of the noise it absorbed.

    Both computed from fields already on the item, so this is an aggregation
    and not a new measurement — and both absent rather than zero when there is
    nothing to say, because "no answer yet" and "an instant answer" are
    different facts.
    """
    quick = _run("probe:a:1", created_at=100.0)
    quick.finished_at = 140.0
    slow = _run("probe:a:2", created_at=100.0, meta={"refires": 3, "follow_ups": ["om_a"]})
    slow.finished_at = 400.0
    items = work.resolve([quick, slow])
    out = work.counts(items)
    # Two items, 40s and 300s to a first answer: the median of a two-item board
    # is the upper of the pair, which is the conservative reading.
    assert out["median_answer_seconds"] == 300.0
    # Six signals arrived — two openings, three re-fires, one follow-up — and
    # became two pieces of work.
    assert out["signals"] == 6 and out["signals_per_item"] == 3.0

    # Nothing answered yet: absent, not zero.
    running = _run("probe:a:3", created_at=100.0)
    running.status = RUNNING
    running.finished_at = None
    out = work.counts(work.resolve([running]))
    assert out["median_answer_seconds"] is None
    assert work.counts([])["signals_per_item"] is None
