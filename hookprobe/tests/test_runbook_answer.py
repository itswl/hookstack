"""A worth-it condition answers a re-fire from the runbook a person vouched for.

The cost lever on the product's own axis: a condition investigated yesterday,
ruled useful, that fires again today should not pay for a cold-start to
re-derive what a person already blessed. The danger it is built around is
"is this re-fire really the same thing", and the answer is the gate — a USEFUL
ruling on a REAL prior run — plus the fact that it never silences: the re-fire
still delivers a card, marked $0 and answered-from-runbook, that a person can
force a real run behind.
"""

from __future__ import annotations

import asyncio
import json
import time

from hookprobe.distill import CASES_MARKER, slug
from hookprobe.runs import COMPLETED, Run, RunStore
from hookprobe.service import RunService
from tests.helpers import FakeEngine, make_settings


def _service(tmp_path, **overrides):
    settings = make_settings(tmp_path, **overrides)
    return RunService(settings, FakeEngine(), RunStore(tmp_path / "results")), settings


def _runbook(tmp_path, title):
    d = tmp_path / ".claude" / "skills" / slug(title)
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"# {title}\n\nrestart the worker and clear the backlog.\n\n{CASES_MARKER}\n\n(cases)\n",
        encoding="utf-8",
    )


def _prior_useful(service, store, title, *, ruled_at, session="probe:ww:1"):
    """A real, finished investigation of `title` a person ruled useful."""
    run = Run(session_key=session, run_id="r-old", status=COMPLETED)
    run.meta = {"title": title}
    run.ruling = "useful"
    run.ruled_at = ruled_at
    store.create(run)
    return run


def _refire(service, title, key="probe:ww:2"):
    """A fresh trigger for the same condition (new session key)."""
    return service.start(
        {"message": f"alert: {title}", "sessionKey": key, "_meta": {"title": title}},
        origin="relay",
    )


async def _finished(service, key, deadline=3.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + deadline
    while loop.time() < end:
        run = service.get(key)
        if run is not None and run.finished:
            return run
        await asyncio.sleep(0.01)
    raise AssertionError("run did not finish")


def _ran_for_real(
    tmp_path, title, *, ruled_at=None, ruling="useful", answered_prior=False, make_runbook=True, days=7, force=False
):
    """Set up a prior + re-fire, drive it to completion in a loop, and return the
    re-fire run. Used by every case that should reach the engine."""

    async def scenario():
        service, _ = _service(tmp_path, runbook_answer_days=days)
        store = service._store
        if make_runbook:
            _runbook(tmp_path, title)
        if ruled_at is not None:
            prior = Run(session_key="probe:ww:1", run_id="r-old", status=COMPLETED)
            prior.meta = {"title": title, **({"answered_from_runbook": True} if answered_prior else {})}
            prior.ruling = ruling
            prior.ruled_at = ruled_at
            store.create(prior)
        payload = {"message": f"alert: {title}", "sessionKey": "probe:ww:2", "_meta": {"title": title}}
        if force:
            payload["force"] = True
        run = service.start(payload, origin="relay")
        answered = bool(run.meta.get("answered_from_runbook"))
        if not answered:
            await _finished(service, "probe:ww:2")
            await service.shutdown()
        return answered

    return asyncio.run(scenario())


def test_a_vouched_condition_answers_from_runbook_at_zero(tmp_path):
    """The payoff: no engine, a report marked answered-from-runbook, carrying the
    procedure a person vouched for."""
    service, _ = _service(tmp_path, runbook_answer_days=7)
    store = service._store
    _runbook(tmp_path, "queue stalled")
    _prior_useful(service, store, "queue stalled", ruled_at=time.time() - 86400)

    run = _refire(service, "queue stalled")
    assert run.meta.get("answered_from_runbook") is True
    report = json.loads(run.text)
    assert report["verdict"] == "known_condition"
    assert "restart the worker" in report["runbook"]
    assert "force" in report["how_to_reinvestigate"]


def test_off_by_default(tmp_path):
    """A stronger claim than declining a not-worth-it condition, so it ships off:
    the same setup with the knob at 0 runs the engine."""
    assert _ran_for_real(tmp_path, "queue stalled", ruled_at=time.time() - 86400, days=0) is False


def test_without_a_useful_ruling_it_runs_for_real(tmp_path):
    """The first investigation, or one nobody blessed, earns no shortcut — a
    person vouching is the whole licence."""
    assert _ran_for_real(tmp_path, "queue stalled", ruled_at=time.time() - 86400, ruling="") is False


def test_a_useless_ruling_does_not_vouch(tmp_path):
    """The gate is USEFUL, not merely ruled. A condition a person judged a miss
    must not answer its own re-fires from the method they rejected."""
    assert _ran_for_real(tmp_path, "queue stalled", ruled_at=time.time() - 86400, ruling="useless") is False


def test_a_runbook_answer_does_not_vouch_for_the_next(tmp_path):
    """Only a REAL useful run licenses this, or a chain of runbook-answers would
    cite itself forever and no real run would ever reverify."""
    assert _ran_for_real(tmp_path, "queue stalled", ruled_at=time.time() - 3600, answered_prior=True) is False, (
        "a runbook answer is not a vouch"
    )


def test_the_licence_lapses_and_a_real_run_reverifies(tmp_path):
    """A vouch older than the window no longer answers — the evidence goes stale,
    and a gate that never re-checks cites last month's case at this month's
    incident. Past the window a real run happens, which if ruled useful refreshes
    it."""
    assert _ran_for_real(tmp_path, "queue stalled", ruled_at=time.time() - 30 * 86400) is False, (
        "the vouch is 30 days old, window is 7"
    )


def test_force_bypasses_it(tmp_path):
    """`force` is how a person says this re-fire looks different — it must reach a
    real run even with a fresh vouch."""
    assert _ran_for_real(tmp_path, "queue stalled", ruled_at=time.time() - 3600, force=True) is False


def test_a_withdrawn_runbook_cannot_answer(tmp_path):
    """The coherence with the useless-withdrawal: no SKILL.md, no answer. A
    condition that stopped being understood stops being answered this way, even
    if an old useful ruling still sits on a run."""
    assert _ran_for_real(tmp_path, "queue stalled", ruled_at=time.time() - 3600, make_runbook=False) is False


# ── and when it cannot fire, why ──────────────────────────────────────────────


def test_readiness_names_the_one_reason_the_cheap_path_is_idle(tmp_path):
    """Configured and inert reads exactly like working. This deployment carries
    21 runbooks — six for the SES conditions it re-investigates from cold every
    time — and the answer path has never fired; the reason took three modules to
    reconstruct. So the node says it, in a sentence, and names ONE reason
    because a list of four blockers is a list nobody acts on."""
    title = "SES bounce rate above 5%"

    # 1. The knob is off, which outranks everything: nothing else matters yet.
    service, _ = _service(tmp_path, runbook_answer_days=0)
    _runbook(tmp_path, title)
    ready = service.runbook_readiness()
    assert ready["library"] == 1 and ready["answer_days"] is None
    assert "switched off" in ready["blocked_by"]

    # 2. Knob on, runbooks present, and nobody has ever ruled — the real state
    # of this deployment, and the one an operator can act on today.
    service, _ = _service(tmp_path, runbook_answer_days=7)
    ready = service.runbook_readiness()
    assert ready["conditions_ready"] == 0
    assert "useful" in ready["blocked_by"] and "vouch" in ready["blocked_by"]

    # 3. A person rules, and the same figures say it can now fire.
    prior = Run(session_key="probe:ww:1", run_id="r-old", status=COMPLETED)
    prior.meta = {"title": title}
    prior.ruling, prior.ruled_at = "useful", time.time()
    service._store.create(prior)
    ready = service.runbook_readiness()
    assert ready["conditions_ready"] == 1 and ready["blocked_by"] is None


def test_readiness_and_the_answer_path_share_one_predicate(tmp_path):
    """Two readers of one rule, and this is the seam that has bitten before:
    `work.py` read a procedure status nothing ever wrote, for months, because
    the rule had two homes. A ruling that does not license an answer must not
    be counted as readiness either."""
    title = "SES bounce rate above 5%"
    service, _ = _service(tmp_path, runbook_answer_days=7)
    _runbook(tmp_path, title)

    # A runbook ANSWER cannot vouch for the next one — the citation loop the
    # answer path refuses. Readiness has to refuse it identically.
    echo = Run(session_key="probe:ww:9", run_id="r-echo", status=COMPLETED)
    echo.meta = {"title": title, "answered_from_runbook": True}
    echo.ruling, echo.ruled_at = "useful", time.time()
    service._store.create(echo)
    assert service.runbook_readiness()["conditions_ready"] == 0

    # And a ruling older than the window licenses nothing, on both paths.
    stale = Run(session_key="probe:ww:8", run_id="r-stale", status=COMPLETED)
    stale.meta = {"title": title}
    stale.ruling, stale.ruled_at = "useful", time.time() - 30 * 86400
    service._store.create(stale)
    assert service.runbook_readiness()["conditions_ready"] == 0
