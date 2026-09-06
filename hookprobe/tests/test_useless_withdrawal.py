"""A ruling of "useless" withdraws the runbook that run distilled.

The defect this closes was measured, not imagined: on the live deployment 13 of
19 rated investigations were ruled useless, and five runbooks turned out to hold
nothing but cases from useless runs — being loaded into every later run as
"what previous investigations checked". The loop was teaching a method already
judged to have taught nothing. auto_distill refuses to write from a run that
failed; "later ruled useless" is the same class of fact arriving after the
write, and until now nothing acted on it.
"""

from __future__ import annotations

import time

from hookprobe import distill


def _runbook(skills_dir, name, sessions):
    """A runbook with one case per session key, shaped like case_block writes."""
    d = skills_dir / name
    d.mkdir(parents=True)

    def _case(i, s):  # shaped exactly like distill.case_block writes one
        return (
            f"<!-- case:start {1000 + i} -->\n"
            f"### 2026-09-06 10:00 · session `{s}`\n\n"
            "1. `kubectl get pods`\n\nfound nothing\n\n"
            "<!-- case:end -->\n"
        )

    cases = "\n".join(_case(i, s) for i, s in enumerate(sessions))
    (d / "SKILL.md").write_text(f"# {name}\n\n<!-- hookprobe:cases -->\n\n{cases}", encoding="utf-8")
    return d


def test_case_sessions_reads_the_provenance_the_wire_rides_on(tmp_path):
    """The session key stamped in each case is the only link from a ruling to
    the knowledge that run taught. If this parse drifts, the whole wire goes
    quiet — so it is pinned against the exact shape case_block writes."""
    from hookprobe.distill import case_block

    class _Run:
        session_key = "probe:judge-notify:42"

    block = case_block(_Run(), steps=["kubectl get pods"], conclusion="found it", at=time.time())
    assert distill.case_sessions(block) == ["probe:judge-notify:42"]
    assert distill.case_sessions("no cases here") == []


def test_a_runbook_of_only_useless_cases_is_withdrawn(tmp_path):
    """Every case from a useless run: the runbook is the bad method itself.
    Withdrawn — SKILL.md gone so it stops being loaded — not merely flagged."""
    skills = tmp_path / ".claude" / "skills"
    _runbook(skills, "redis-key-surge", ["s1", "s2"])
    rulings = {"s1": "useless", "s2": "useless"}

    out = distill.reconsider_after_useless(skills, "s1", rulings.get, at=time.time())
    assert out == [{"runbook": "redis-key-surge", "action": "withdrawn", "cases": 2}]
    assert not (skills / "redis-key-surge" / "SKILL.md").exists(), "no longer loaded into a run"


def test_a_runbook_with_a_good_case_is_flagged_not_removed(tmp_path):
    """It holds knowledge that may still be worth having, so nothing is removed —
    it is marked for a person. Removing a runbook because ONE of its cases was
    useless would throw away the four incidents it got right."""
    skills = tmp_path / ".claude" / "skills"
    book = _runbook(skills, "ses-bounce", ["good", "bad"])
    rulings = {"good": "useful", "bad": "useless"}

    out = distill.reconsider_after_useless(skills, "bad", rulings.get, at=time.time())
    assert out[0]["action"] == "flagged" and out[0]["useless"] == 1
    assert (book / "SKILL.md").exists(), "knowledge that may be good is kept"


def test_an_unruled_sibling_case_is_enough_to_keep_it(tmp_path):
    """ "Every case useless" means every case, not most. An unruled case is not a
    useless one, and might yet be blessed — so its presence blocks withdrawal."""
    skills = tmp_path / ".claude" / "skills"
    _runbook(skills, "mixed", ["bad", "unruled"])
    rulings = {"bad": "useless", "unruled": ""}
    out = distill.reconsider_after_useless(skills, "bad", rulings.get, at=time.time())
    assert out[0]["action"] == "flagged"


def test_a_withdrawal_is_reversible_from_history(tmp_path):
    """Removing the manifest is safe only because history keeps it. The snapshot
    is what makes withdrawal an undo-able act rather than a delete."""
    skills = tmp_path / ".claude" / "skills"
    book = _runbook(skills, "gone", ["s1"])
    distill.reconsider_after_useless(skills, "s1", {"s1": "useless"}.get, at=time.time())
    history = sorted((book / "history").glob("*-SKILL.md"))
    assert history, "the manifest was snapshotted before removal"
    assert "case:start" in history[-1].read_text(encoding="utf-8"), "and it is the real one"


def test_a_runbook_that_run_never_touched_is_left_alone(tmp_path):
    """The ruling reaches only the runbooks whose cases name this session."""
    skills = tmp_path / ".claude" / "skills"
    other = _runbook(skills, "unrelated", ["someone-else"])
    out = distill.reconsider_after_useless(skills, "s1", {"s1": "useless"}.get, at=time.time())
    assert out == []
    assert (other / "SKILL.md").exists()


def test_a_useless_press_withdraws_end_to_end_and_records_a_distill_regret(tmp_path):
    """The whole wire, from the button a person presses to the runbook that stops
    being loaded — and the distill-class regret it leaves in the automation
    record, which is what keeps that auto-writing class honestly below auto_apply.
    """
    from hookprobe import automation
    from hookprobe.runs import COMPLETED, Run, RunStore
    from hookprobe.service import RunService
    from tests.helpers import FakeEngine, make_settings

    settings = make_settings(tmp_path)
    store = RunStore(tmp_path / "results")
    service = RunService(settings, FakeEngine(), store)

    run = Run(session_key="probe:judge:7", run_id="r1", status=COMPLETED)
    store.create(run)
    _runbook(tmp_path / ".claude" / "skills", "bad-method", ["probe:judge:7"])

    service.record_ruling("probe:judge:7", "useless")

    assert not (tmp_path / ".claude" / "skills" / "bad-method" / "SKILL.md").exists(), "withdrawn"
    # The withdrawal is a distill-class regret in the automation ledger — the
    # after-the-fact signal that an auto-written runbook was wrong. Asserted on
    # the ledger directly: whether it also shows in stats() depends on the
    # runbook having a matching install proposal, and a hand-made or pre-feature
    # runbook legitimately has none, but the regret is real either way.
    regrets = [r for r in automation.ledger(tmp_path, "distill") if r["event"] == "regretted"]
    assert len(regrets) == 1 and regrets[0]["id"] == "bad-method"


def test_a_useful_press_touches_no_runbook(tmp_path):
    """Only useless reconsiders. A useful ruling is the loop working, not a
    regret, and must not go rummaging through the library."""
    from hookprobe.runs import COMPLETED, Run, RunStore
    from hookprobe.service import RunService
    from tests.helpers import FakeEngine, make_settings

    settings = make_settings(tmp_path)
    store = RunStore(tmp_path / "results")
    service = RunService(settings, FakeEngine(), store)
    run = Run(session_key="probe:judge:8", run_id="r1", status=COMPLETED)
    store.create(run)
    book = _runbook(tmp_path / ".claude" / "skills", "good-method", ["probe:judge:8"])

    service.record_ruling("probe:judge:8", "useful")
    assert (book / "SKILL.md").exists(), "a useful ruling leaves the library alone"
