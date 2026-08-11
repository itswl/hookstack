import asyncio
import json

import pytest

from hookprobe.runs import COMPLETED, FAILED, RunStore
from hookprobe.service import RunService
from tests.helpers import FakeEngine, make_settings


async def _wait_finished(service: RunService, key: str, deadline: float = 3.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + deadline
    while loop.time() < end:
        run = service.get(key)
        if run is not None and run.finished:
            return run
        await asyncio.sleep(0.01)
    raise AssertionError("run did not finish in time")


def test_successful_run_completes(tmp_path) -> None:
    async def scenario() -> None:
        engine = FakeEngine()
        service = RunService(make_settings(tmp_path), engine, RunStore(tmp_path / "results"))
        run = service.start({"message": "analyze this", "sessionKey": "hook:deep-analysis:x:1"})
        assert run.run_id
        done = await _wait_finished(service, "hook:deep-analysis:x:1")
        assert done.status == COMPLETED
        assert done.text == '{"summary": "ok"}'
        assert done.message_count == 3
        assert done.cost_usd == 0.5

    asyncio.run(scenario())


def test_retrigger_same_session_is_idempotent(tmp_path) -> None:
    async def scenario() -> None:
        engine = FakeEngine(delay=0.05)
        service = RunService(make_settings(tmp_path), engine, RunStore(tmp_path / "results"))
        first = service.start({"message": "analyze", "sessionKey": "hook:k"})
        second = service.start({"message": "analyze", "sessionKey": "hook:k"})
        assert first.run_id == second.run_id
        await _wait_finished(service, "hook:k")
        assert engine.calls == 1

    asyncio.run(scenario())


def test_engine_crash_yields_failure_report(tmp_path) -> None:
    async def scenario() -> None:
        engine = FakeEngine(exc=RuntimeError("boom"))
        service = RunService(make_settings(tmp_path), engine, RunStore(tmp_path / "results"))
        service.start({"message": "analyze", "sessionKey": "hook:crash"})
        done = await _wait_finished(service, "hook:crash")
        assert done.status == FAILED
        assert done.error is not None and "boom" in done.error
        report = json.loads(done.text)
        assert "hookprobe run failed" in report["summary"]
        assert report["root_cause"]["status"] == "unknown"
        assert report["confidence"] == 0.0

    asyncio.run(scenario())


def test_timeout_yields_failure_report(tmp_path) -> None:
    async def scenario() -> None:
        engine = FakeEngine(delay=5.0)
        service = RunService(make_settings(tmp_path), engine, RunStore(tmp_path / "results"))
        service.start({"message": "analyze", "sessionKey": "hook:slow", "timeoutSeconds": 1})
        done = await _wait_finished(service, "hook:slow", deadline=3.0)
        assert done.status == FAILED
        assert done.error is not None and "timed out" in done.error

    asyncio.run(scenario())


def test_empty_message_rejected(tmp_path) -> None:
    async def scenario() -> None:
        service = RunService(make_settings(tmp_path), FakeEngine(), RunStore(tmp_path / "results"))
        with pytest.raises(ValueError):
            service.start({"message": "   ", "sessionKey": "hook:empty"})

    asyncio.run(scenario())


def test_completed_run_survives_restart(tmp_path) -> None:
    async def scenario() -> None:
        settings = make_settings(tmp_path)
        service = RunService(settings, FakeEngine(), RunStore(tmp_path / "results"))
        service.start({"message": "analyze", "sessionKey": "hook:persist"})
        await _wait_finished(service, "hook:persist")

        # A fresh store over the same directory simulates a container restart.
        reborn = RunService(settings, FakeEngine(), RunStore(tmp_path / "results"))
        recovered = reborn.get("hook:persist")
        assert recovered is not None
        assert recovered.finished
        assert recovered.text == '{"summary": "ok"}'

    asyncio.run(scenario())


def test_concurrency_cap_is_respected(tmp_path) -> None:
    async def scenario() -> None:
        engine = FakeEngine(delay=0.05)
        settings = make_settings(tmp_path, max_concurrent=1)
        service = RunService(settings, engine, RunStore(tmp_path / "results"))
        service.start({"message": "a", "sessionKey": "hook:c1"})
        service.start({"message": "b", "sessionKey": "hook:c2"})
        await _wait_finished(service, "hook:c1")
        await _wait_finished(service, "hook:c2")
        assert engine.calls == 2
        assert engine.max_running == 1

    asyncio.run(scenario())


def test_continue_run_resumes_engine_session(tmp_path) -> None:
    async def scenario() -> None:
        engine = FakeEngine()
        service = RunService(make_settings(tmp_path), engine, RunStore(tmp_path / "results"))
        service.start({"message": "analyze", "sessionKey": "hook:follow"})
        first = await _wait_finished(service, "hook:follow")
        assert first.engine_session_id == "sdk-session-1"
        first_run_id = first.run_id

        service.continue_run("hook:follow", {"message": "dig into node capacity"})
        done = await _wait_finished(service, "hook:follow")
        assert engine.calls == 2
        assert engine.resumes == [None, "sdk-session-1"]
        assert engine.messages[1] == "dig into node capacity"
        assert done.run_id != first_run_id
        assert [t["message"] for t in done.turns] == ["analyze", "dig into node capacity"]
        assert done.turns[0]["text"] == '{"summary": "ok"}'

    asyncio.run(scenario())


def test_continue_guards(tmp_path) -> None:
    from hookprobe.engine import EngineResult
    from hookprobe.service import NotResumableError, RunBusyError

    async def scenario() -> None:
        engine = FakeEngine(delay=0.2)
        service = RunService(make_settings(tmp_path), engine, RunStore(tmp_path / "results"))

        with pytest.raises(LookupError):
            service.continue_run("hook:missing", {"message": "hi"})

        service.start({"message": "analyze", "sessionKey": "hook:busy"})
        with pytest.raises(RunBusyError):
            service.continue_run("hook:busy", {"message": "hi"})
        await _wait_finished(service, "hook:busy")

        with pytest.raises(ValueError):
            service.continue_run("hook:busy", {"message": "   "})

        # A run whose engine never reported a session id cannot be resumed.
        no_session = FakeEngine(result=EngineResult(text='{"summary": "ok"}', message_count=1))
        bare = RunService(make_settings(tmp_path), no_session, RunStore(tmp_path / "r2"))
        bare.start({"message": "analyze", "sessionKey": "hook:bare"})
        await _wait_finished(bare, "hook:bare")
        with pytest.raises(NotResumableError):
            bare.continue_run("hook:bare", {"message": "hi"})

    asyncio.run(scenario())


def test_failed_follow_up_keeps_previous_answer(tmp_path) -> None:
    async def scenario() -> None:
        engine = FakeEngine()
        service = RunService(make_settings(tmp_path), engine, RunStore(tmp_path / "results"))
        service.start({"message": "analyze", "sessionKey": "hook:keep"})
        await _wait_finished(service, "hook:keep")

        engine.exc = RuntimeError("follow-up exploded")
        service.continue_run("hook:keep", {"message": "more"})
        done = await _wait_finished(service, "hook:keep")
        assert done.status == FAILED
        # The original answer survives as its own turn; the failure is a new one.
        assert done.turns[0]["text"] == '{"summary": "ok"}'
        assert done.turns[1]["error"] is not None and "follow-up exploded" in done.turns[1]["error"]

    asyncio.run(scenario())


def test_timeout_clamped_to_max(tmp_path) -> None:
    async def scenario() -> None:
        settings = make_settings(tmp_path)  # default 5s, max 10s
        service = RunService(settings, FakeEngine(), RunStore(tmp_path / "results"))
        assert service._clamp_timeout(99999) == settings.max_timeout_seconds
        assert service._clamp_timeout(None) == settings.default_timeout_seconds
        assert service._clamp_timeout(-5) == settings.default_timeout_seconds
        # The default itself is clamped when it exceeds the ceiling.
        tight = RunService(make_settings(tmp_path, max_timeout_seconds=2), FakeEngine(), RunStore(tmp_path / "r2"))
        assert tight._clamp_timeout(None) == 2

    asyncio.run(scenario())
