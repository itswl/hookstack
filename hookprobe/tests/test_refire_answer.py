"""A re-fire a few hours after a REAL investigation answers from that
investigation, and needs no ruling to do it.

Measured on production 2026-09-14: one SES condition re-fired every four hours
through a day — six cold starts at $0.65–2.71 reaching one finding, five of them
proposing the same read-only check — and the patrol's `useful` that would have
licensed the vouched path landed two days later. The other two $0 paths wait for
a verdict; this one waits for nothing but a clean real run inside a window, at
the same level, with no recovery between, and it is bounded so a flapping alert
still buys a real look. Every clause that holds the gate open is tested here,
because every clause is a reason to pay.
"""

from __future__ import annotations

import asyncio
import json
import time

from hookprobe import service as service_module
from hookprobe.distill import CASES_MARKER, slug
from hookprobe.runs import COMPLETED, FAILED, Run, RunStore
from hookprobe.service import RunService
from tests.helpers import FakeEngine, make_settings

TITLE = "[SES] bounce volume high (24h)"
FINDING = "**结论：** 过去 1 小时退信 271 次，约为基线 3.3 倍；根因无法在本环境确认。"


def _service(tmp_path, **overrides):
    settings = make_settings(tmp_path, **overrides)
    return RunService(settings, FakeEngine(), RunStore(tmp_path / "results"))


def _prior(
    store,
    *,
    key,
    hours_ago,
    level="high",
    source="grafana",
    status=COMPLETED,
    error=None,
    text=FINDING,
    answered=False,
    recovered=False,
    patrol=False,
):
    """A finished run of the condition, `hours_ago` hours back."""
    run = Run(session_key=key, run_id=f"r-{key[-3:]}", status=status, text=text, error=error)
    run.meta = {"title": TITLE, "level": level, "source": source, "event_id": key}
    if answered:
        run.meta["answered_from_runbook"] = True
    if recovered:
        run.meta["recovered_at"] = time.time() - hours_ago * 3600 + 60
    if patrol:
        run.meta["patrol"] = "run-rulings"
    run.created_at = time.time() - hours_ago * 3600 - 30
    run.finished_at = time.time() - hours_ago * 3600
    store.create(run)
    return run


def _runbook(tmp_path):
    d = tmp_path / ".claude" / "skills" / slug(TITLE)
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"# {TITLE}\n\ncheck the sending account status.\n\n{CASES_MARKER}\n\n(cases)\n")


def _payload(*, level="high", source="grafana", extra=None, force=False):
    meta = {"title": TITLE, "level": level, "source": source, "event_id": "evt-new", **(extra or {})}
    payload = {"message": f"alert: {TITLE}", "sessionKey": "probe:ww:new", "_meta": meta}
    if force:
        payload["force"] = True
    return payload


async def _finished(service, key, deadline=3.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + deadline
    while loop.time() < end:
        run = service.get(key)
        if run is not None and run.finished:
            return run
        await asyncio.sleep(0.01)
    raise AssertionError("run did not finish")


def _paid_for_a_real_run(tmp_path, arrange, *, hours=24, **payload_kw) -> bool:
    """Arrange the store, fire the re-fire, and say whether the engine ran."""

    async def scenario():
        service = _service(tmp_path, refire_answer_hours=hours)
        arrange(service._store)
        run = service.start(_payload(**payload_kw), origin="relay")
        answered = bool(run.meta.get("answered_from_runbook"))
        if not answered:
            await _finished(service, "probe:ww:new")
            await service.shutdown()
        return not answered

    return asyncio.run(scenario())


# ── the payoff ────────────────────────────────────────────────────────────────


def test_a_refire_inside_the_window_answers_from_the_last_real_run(tmp_path):
    """No engine, $0, and the report carries the finding it reused, names the run
    it reused it from, and counts itself — with no ruling anywhere."""
    service = _service(tmp_path, refire_answer_hours=24)
    anchor = _prior(service._store, key="probe:ww:001", hours_ago=4)

    run = service.start(_payload(), origin="relay")

    assert run.meta.get("answered_from_runbook") is True
    assert run.meta.get("refire_of") == anchor.session_key
    assert run.status == COMPLETED and run.cost_usd == 0.0
    report = json.loads(run.text)
    assert report["verdict"] == "recurring_condition"
    assert report["refire_of"] == anchor.session_key
    assert report["refires_since_anchor"] == 1
    assert "退信 271 次" in report["standing_finding"]
    assert report["runbook"] == "", "no runbook was distilled; the report alone answers"
    assert "force" in report["how_to_reinvestigate"]
    assert "answered from the runbook" in run.distilled.get("skipped", "")


def test_the_runbook_rides_along_when_one_exists(tmp_path):
    service = _service(tmp_path, refire_answer_hours=24)
    _runbook(tmp_path)
    _prior(service._store, key="probe:ww:001", hours_ago=4)
    report = json.loads(service.start(_payload(), origin="relay").text)
    assert "check the sending account status" in report["runbook"]
    assert CASES_MARKER not in report["runbook"]


def test_answers_count_up_until_the_bound(tmp_path):
    """The SES shape: one real run, then a re-fire every four hours. Each answer
    says which number it is, and the tenth is the last."""
    service = _service(tmp_path, refire_answer_hours=48)
    _prior(service._store, key="probe:ww:001", hours_ago=30)
    for n in range(service_module._REFIRE_ANSWER_MAX - 1):
        _prior(service._store, key=f"probe:ww:a{n:02d}", hours_ago=26 - n, answered=True)
    report = json.loads(service.start(_payload(), origin="relay").text)
    assert report["refires_since_anchor"] == service_module._REFIRE_ANSWER_MAX


# ── every clause that makes it pay instead ────────────────────────────────────


def test_off_by_default(tmp_path):
    """Answering from a report is a claim about the world; the default makes none."""
    assert _paid_for_a_real_run(tmp_path, lambda s: _prior(s, key="probe:ww:001", hours_ago=4), hours=0)


def test_outside_the_window_a_real_run_reverifies(tmp_path):
    assert _paid_for_a_real_run(tmp_path, lambda s: _prior(s, key="probe:ww:001", hours_ago=30), hours=24)


def test_a_changed_level_is_a_different_question(tmp_path):
    """`high` four hours ago and `critical` now is not the same re-fire."""
    assert _paid_for_a_real_run(tmp_path, lambda s: _prior(s, key="probe:ww:001", hours_ago=4), level="critical")


def test_a_recovery_since_the_anchor_starts_a_new_episode(tmp_path):
    """The condition ended and fired again: the anchor's finding is about the
    episode that closed. Checked on the anchor itself…"""
    assert _paid_for_a_real_run(tmp_path, lambda s: _prior(s, key="probe:ww:001", hours_ago=4, recovered=True))


def test_a_recovery_recorded_on_a_later_answer_also_ends_the_licence(tmp_path):
    """…and on every run since, because `record_recovery` annotates the NEWEST
    run of the condition, which after the first answer is an answer."""

    def arrange(store):
        _prior(store, key="probe:ww:001", hours_ago=8)
        _prior(store, key="probe:ww:a01", hours_ago=4, answered=True, recovered=True)

    assert _paid_for_a_real_run(tmp_path, arrange)


def test_a_runbook_answer_never_anchors_the_next(tmp_path):
    """Only a run that looked vouches, or a chain of answers would cite itself
    past the window and forever."""
    assert _paid_for_a_real_run(tmp_path, lambda s: _prior(s, key="probe:ww:a01", hours_ago=4, answered=True))


def test_a_newer_failed_real_run_forfeits_the_anchor(tmp_path):
    """The last real look did not finish, so this one must."""

    def arrange(store):
        _prior(store, key="probe:ww:001", hours_ago=6)
        _prior(store, key="probe:ww:002", hours_ago=1, status=FAILED, error="provider 502", text="")

    assert _paid_for_a_real_run(tmp_path, arrange)


def test_the_bound_buys_a_real_look(tmp_path):
    """Ten answers on one anchor, and the eleventh re-fire pays — a flapping
    alert with no recovery between must not ride one report for a whole window."""

    def arrange(store):
        _prior(store, key="probe:ww:001", hours_ago=20)
        for n in range(service_module._REFIRE_ANSWER_MAX):
            _prior(store, key=f"probe:ww:a{n:02d}", hours_ago=19 - n, answered=True)

    assert _paid_for_a_real_run(tmp_path, arrange, hours=48)


def test_a_different_source_is_a_different_condition(tmp_path):
    """Identity is (source, title), the pair `same_alert` and `record_recovery`
    already key on."""
    assert _paid_for_a_real_run(tmp_path, lambda s: _prior(s, key="probe:ww:001", hours_ago=4), source="cloudwatch")


def test_never_for_work_a_person_asked_for_or_a_patrol(tmp_path):
    """A task, a brief, a chat question and a patrol are not alerts re-firing."""
    for extra in ({"kind": "task"}, {"kind": "brief"}, {"asked_by": "ou_x"}, {"thread_root": "om_x"}, {"patrol": "x"}):
        assert _paid_for_a_real_run(tmp_path, lambda s: _prior(s, key="probe:ww:001", hours_ago=4), extra=extra), extra


def test_a_patrol_run_of_the_same_title_is_not_an_anchor(tmp_path):
    assert _paid_for_a_real_run(tmp_path, lambda s: _prior(s, key="probe:ww:001", hours_ago=4, patrol=True))


def test_force_bypasses_it(tmp_path):
    """`force` is how a person says this re-fire looks different."""
    assert _paid_for_a_real_run(tmp_path, lambda s: _prior(s, key="probe:ww:001", hours_ago=4), force=True)
