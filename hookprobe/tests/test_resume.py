"""A restart continues an investigation instead of throwing it away — within bounds.

The engine's transcript lives on the data volume, not in this process, so a
session id is a handle to everything an interrupted attempt gathered. Before
this, every run in flight at a restart became a failure an operator re-asked by
hand, paying twice. The bounds matter more than the feature: this is the one
path that spends money with nobody asking, so each of them has a test.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from hookprobe.runs import FAILED, RUNNING, Run, RunStore
from hookprobe.service import RunService
from tests.helpers import FakeEngine, make_settings


def _orphan(results: RunStore, *, session: str | None = "sdk-session-1", resumes: int = 0) -> Run:
    """What the disk looks like after a crash: a checkpointed run, no process."""
    run = Run(session_key="probe:inbound:31", run_id="r1", origin="relay", current_message="investigate")
    run.meta = {"title": "Payment gateway 5xx", "level": "high", "source": "inbound", "event_id": 31}
    if resumes:
        run.meta["resumes"] = resumes
    run.engine_session_id = session
    results.checkpoint(run)
    return run


async def _settled(service: RunService, key: str = "probe:inbound:31") -> Run:
    for _ in range(400):
        run = service.get(key)
        if run is not None and run.finished:
            return run
        await asyncio.sleep(0.01)
    raise AssertionError("run never settled")


def test_an_interrupted_run_continues_its_own_engine_session(tmp_path) -> None:
    store = RunStore(tmp_path / "results")
    _orphan(store)

    async def next_boot() -> None:
        engine = FakeEngine()
        service = RunService(make_settings(tmp_path), engine, RunStore(tmp_path / "results"))
        assert service.recover_orphans() == (1, 0)
        run = await _settled(service)

        assert engine.resumes == ["sdk-session-1"], "continued, not started cold"
        assert "do not start the investigation over" in engine.messages[-1]
        assert run.status == "completed" and run.meta["resumes"] == 1
        # The lost attempt is a turn of its own, priced at "nobody counted" —
        # the provider billed whatever it billed and no result came back to say.
        assert [t["error"] for t in run.turns] == ["interrupted by a restart", None]
        assert run.turns[0]["cost_usd"] is None and run.turns[1]["cost_usd"] == 0.5

    asyncio.run(next_boot())


def test_a_run_with_no_engine_session_still_fails_and_reports(tmp_path) -> None:
    """The old behaviour, kept: silence would break "failure completes the loop"."""
    store = RunStore(tmp_path / "results")
    _orphan(store, session=None)

    async def next_boot() -> None:
        service = RunService(make_settings(tmp_path), FakeEngine(), RunStore(tmp_path / "results"))
        assert service.recover_orphans() == (0, 1)
        run = service.get("probe:inbound:31")
        assert run is not None and run.status == FAILED and "interrupted by a restart" in (run.error or "")

    asyncio.run(next_boot())


def test_one_resume_per_run_so_a_crash_loop_is_not_a_spend_loop(tmp_path) -> None:
    """The counter is persisted on the run. A process that dies on every boot
    buys one continuation, not one per restart, forever."""
    store = RunStore(tmp_path / "results")
    _orphan(store, resumes=1)

    async def next_boot() -> None:
        engine = FakeEngine()
        service = RunService(make_settings(tmp_path), engine, RunStore(tmp_path / "results"))
        assert service.recover_orphans() == (0, 1)
        assert engine.calls == 0, "no second continuation was bought"

    asyncio.run(next_boot())


def test_the_budget_breaker_covers_this_door_too(tmp_path) -> None:
    store = RunStore(tmp_path / "results")
    spent = Run(session_key="probe:inbound:1", run_id="r0", status="completed")
    spent.turns = [{"cost_usd": 9.0, "finished_at": time.time()}]
    spent.finished_at = time.time()
    store.create(spent)
    store.finish(spent)
    _orphan(store)

    async def next_boot() -> None:
        engine = FakeEngine()
        settings = make_settings(tmp_path, budget_usd=1.0, budget_window_hours=24)
        service = RunService(settings, engine, RunStore(tmp_path / "results"))
        assert service.recover_orphans() == (0, 1)
        assert engine.calls == 0, "a restart must not spend past the ceiling"

    asyncio.run(next_boot())


def test_a_deployment_can_refuse_to_spend_on_its_own_after_a_restart(tmp_path) -> None:
    store = RunStore(tmp_path / "results")
    _orphan(store)

    async def next_boot() -> None:
        engine = FakeEngine()
        service = RunService(make_settings(tmp_path, resume_interrupted=False), engine, RunStore(tmp_path / "results"))
        assert service.recover_orphans() == (0, 1)
        assert engine.calls == 0

    asyncio.run(next_boot())


@pytest.mark.anyio
async def test_the_session_id_is_on_disk_before_the_turn_ends(tmp_path) -> None:
    """The enabler, and the reason a first turn is resumable at all.

    The id used to be recorded from the engine's RESULT, so a process killed
    mid-turn left a run with no handle to continue — the investigation thrown
    away because its id arrived one message too late. The runtime publishes it
    as an event now, and this asserts it reaches the checkpoint on disk while
    the turn is still running.
    """
    store = RunStore(tmp_path / "results")
    seen: asyncio.Event = asyncio.Event()

    class Slow(FakeEngine):
        async def run(self, *, message, session_key, resume=None, on_event=None, **kw):  # type: ignore[override]
            if on_event:
                on_event({"type": "session", "id": "sdk-mid-turn"})
            seen.set()
            await asyncio.sleep(0.2)
            return await super().run(message=message, session_key=session_key, resume=resume, on_event=None, **kw)

    service = RunService(make_settings(tmp_path), Slow(), store)
    run = service.start({"message": "look", "sessionKey": "probe:inbound:9"})
    await asyncio.wait_for(seen.wait(), timeout=2)
    await asyncio.sleep(0.02)

    on_disk = RunStore(tmp_path / "results")._load("probe:inbound:9")
    assert on_disk is not None and on_disk.status == RUNNING
    assert on_disk.engine_session_id == "sdk-mid-turn", "a crash right now would still be resumable"
    await _settled(service, "probe:inbound:9")
    assert run.engine_session_id


def test_a_person_can_retry_a_failed_run_and_it_continues_what_it_gathered(tmp_path) -> None:
    """Human takeover. Until this, the only way back from a failure was to
    re-fire the alert or retype the question — the operator carrying what the
    service already knew."""

    async def scenario() -> None:
        engine = FakeEngine(exc=RuntimeError("provider said no"))
        service = RunService(make_settings(tmp_path), engine, RunStore(tmp_path / "results"))
        run = service.start({"message": "why is the gateway 5xx?", "sessionKey": "probe:inbound:5"})
        await _settled(service, "probe:inbound:5")
        assert run.status == FAILED

        # A failure that left no engine session re-asks the opening question.
        engine.exc = None
        again = service.retry("probe:inbound:5", by="operator")
        assert again.meta["retries"] == 1 and again.meta["retried_by"] == "operator"
        done = await _settled(service, "probe:inbound:5")
        assert done.status == "completed"
        assert engine.messages[-1] == "why is the gateway 5xx?" and engine.resumes[-1] is None

        # A failure that DID leave one continues it instead of paying twice.
        done.status, done.error, done.finished_at = FAILED, "timed out", time.time()
        RunStore(tmp_path / "results").finish(done)
        third = service.retry("probe:inbound:5")
        assert third.meta["retries"] == 2
        await _settled(service, "probe:inbound:5")
        assert engine.resumes[-1] == "sdk-session-1"
        assert "do not start the investigation over" in engine.messages[-1]

    asyncio.run(scenario())


def test_only_a_failed_run_can_be_retried(tmp_path) -> None:
    async def scenario() -> None:
        service = RunService(make_settings(tmp_path), FakeEngine(), RunStore(tmp_path / "results"))
        service.start({"message": "look", "sessionKey": "probe:inbound:6"})
        done = await _settled(service, "probe:inbound:6")
        assert done.status == "completed"
        with pytest.raises(ValueError, match="only a failed run"):
            service.retry("probe:inbound:6")
        with pytest.raises(LookupError):
            service.retry("probe:nobody:1")

    asyncio.run(scenario())
