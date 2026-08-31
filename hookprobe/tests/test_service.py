"""The run service: one engine call per task, and what happens when it fails.

Every test here injects a fake engine (tests never import the SDK), so what is
under test is the bookkeeping around the call — checkpointing, settling a
crash or a timeout as a well-formed failure report, and resuming a session.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from hookprobe.engine import engine_error
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
        assert done.model == "claude-opus-5"
        # The engine's accounting lands on the turn record for the UI to show.
        assert done.turns[0]["usage"]["output_tokens"] == 34
        assert done.turns[0]["model_usage"] == {"claude-opus-5": {"inputTokens": 12}}
        assert done.turns[0]["duration_ms"] == 1234

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


def test_two_long_session_keys_keep_their_own_case_files(tmp_path) -> None:
    """The case file's name used to be the key truncated to 200 characters, and
    the event door builds keys from an unbounded source and event id. Two long
    keys differing only past the cut named one file: each finish() overwrote the
    other's report, and after a restart a lookup for one answered with the
    other's — the wrong investigation under the right name."""

    async def scenario() -> None:
        settings = make_settings(tmp_path)
        results = tmp_path / "results"
        service = RunService(settings, FakeEngine(), RunStore(results))
        stem = "probe:inbound:" + "x" * 240
        first, second = stem + "A", stem + "B"
        service.start({"message": "the first alert", "sessionKey": first})
        service.start({"message": "the second alert", "sessionKey": second})
        await _wait_finished(service, first)
        await _wait_finished(service, second)
        assert len(list(results.glob("*.json"))) == 2

        # A key that fits keeps its literal name: the case files already on the
        # volume were written that way and have to stay readable.
        service.start({"message": "a short one", "sessionKey": "probe:inbound:7"})
        await _wait_finished(service, "probe:inbound:7")
        assert (results / "probe:inbound:7.json").is_file()

        # A fresh store over the same directory: every answer comes off disk.
        reborn = RunService(settings, FakeEngine(), RunStore(results))
        assert reborn.get(first).turns[0]["message"] == "the first alert"
        assert reborn.get(second).turns[0]["message"] == "the second alert"

    asyncio.run(scenario())


def test_a_timed_out_follow_up_is_not_billed_the_previous_turn(tmp_path) -> None:
    """Two accounting errors met on this path. The follow-up reset the text and
    the error but not the cost, so a turn that died before the engine reported
    anything re-recorded the first turn's bill — a $2 investigation plus a
    failed follow-up read as $4 in the window and in the session total.

    The opposite error used to survive here and no longer does: the engine
    reports dollars only with its final message, so a turn the wall clock KILLED
    recorded no cost at all. It is now interrupted instead of killed, which lets
    that message arrive — so the timeout keeps its verdict and gains its bill."""
    from hookprobe.app import _summary  # the session total the console shows
    from hookprobe.engine import EngineResult

    async def scenario() -> None:
        engine = FakeEngine(
            result=EngineResult(text='{"summary": "ok"}', message_count=2, cost_usd=2.0, session_id="sdk-1")
        )
        service = RunService(make_settings(tmp_path), engine, RunStore(tmp_path / "results"))
        service.start({"message": "analyze", "sessionKey": "hook:bill"})
        first = await _wait_finished(service, "hook:bill")
        assert first.turns[0]["cost_usd"] == 2.0

        engine.delay = 5.0  # the follow-up never reaches a result
        service.continue_run("hook:bill", {"message": "more", "timeoutSeconds": 1})
        done = await _wait_finished(service, "hook:bill", deadline=4.0)

        # Still a timeout — the clock ran out, and an interrupted turn reporting
        # a clean finish must not read as an answer.
        assert done.status == FAILED and done.error is not None and "timed out" in done.error
        # But it is no longer FREE. The engine is interrupted instead of killed,
        # so the SDK's final message arrives and the turn carries what it spent.
        # Before this, the priciest failures there are — a turn that ran the full
        # clock — recorded None and the budget breaker undercounted exactly them.
        assert done.turns[1]["cost_usd"] == engine.interrupted_cost
        assert service.window_spend() == 2.0 + engine.interrupted_cost
        assert _summary(done)["cost_usd"] == 2.0 + engine.interrupted_cost
        # And nothing is unpriced any more, which is the whole point: the
        # unpriced_turns figure now counts only what the interrupt could not save.
        assert service.window_unpriced() == 0
        assert engine.interrupts == 1, "the turn was asked to stop, not killed"

    asyncio.run(scenario())


def test_a_turn_that_really_cost_nothing_still_counts_as_counted(tmp_path) -> None:
    """0.0 is a price; None is the absence of one. _summary filtered turn costs
    by truthiness, so a genuinely free turn — a budget refusal — was dropped and
    the session total silently fell back to the run-level figure, erasing the
    one distinction the rest of this accounting works to keep."""
    from hookprobe.app import _summary
    from hookprobe.runs import Run

    free = Run(session_key="k", run_id="r", current_message="m")
    free.turns = [{"message": "m", "cost_usd": 0.0}]
    free.cost_usd = 7.77
    assert _summary(free)["cost_usd"] == 0.0, "a counted zero must outrank a stale run-level total"

    unpriced = Run(session_key="k", run_id="r", current_message="m")
    unpriced.turns = [{"message": "m", "cost_usd": None}]
    unpriced.cost_usd = 2.0
    assert _summary(unpriced)["cost_usd"] == 2.0, "nothing was counted, so the fallback still applies"


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


def test_process_events_land_on_the_turn(tmp_path) -> None:
    async def scenario() -> None:
        engine = FakeEngine(
            events=[
                {"type": "tool_use", "name": "Bash", "detail": "kubectl get pods -n prod"},
                {"type": "text", "text": "checking pod states"},
                {
                    "type": "tool_use",
                    "name": "TodoWrite",
                    "detail": "",
                    "todos": [{"content": "check nodes", "status": "pending"}],
                },
            ]
        )
        service = RunService(make_settings(tmp_path), engine, RunStore(tmp_path / "results"))
        service.start({"message": "analyze", "sessionKey": "hook:events"})
        done = await _wait_finished(service, "hook:events")
        events = done.turns[0]["events"]
        assert [e["type"] for e in events] == ["tool_use", "text", "tool_use"]
        assert events[0]["name"] == "Bash"
        assert events[0]["ts"] > 0  # service stamps arrival time
        assert events[2]["todos"] == [{"content": "check nodes", "status": "pending"}]

    asyncio.run(scenario())


def test_stop_cancels_the_running_turn(tmp_path) -> None:
    from tests.helpers import GatedEngine

    async def scenario() -> None:
        engine = GatedEngine()  # never released — only stop can end this turn
        service = RunService(make_settings(tmp_path), engine, RunStore(tmp_path / "results"))
        service.start({"message": "analyze", "sessionKey": "hook:stopme"})
        await asyncio.sleep(0.05)  # let the task actually start
        service.stop("hook:stopme")
        done = await _wait_finished(service, "hook:stopme")
        assert done.status == FAILED
        assert done.error == "stopped by operator"
        report = json.loads(done.text)
        assert "stopped by operator" in report["summary"]

    asyncio.run(scenario())


def test_stop_guards(tmp_path) -> None:
    from hookprobe.service import NoTurnRunningError

    async def scenario() -> None:
        service = RunService(make_settings(tmp_path), FakeEngine(), RunStore(tmp_path / "results"))
        with pytest.raises(LookupError):
            service.stop("hook:never")
        service.start({"message": "analyze", "sessionKey": "hook:done"})
        await _wait_finished(service, "hook:done")
        with pytest.raises(NoTurnRunningError):
            service.stop("hook:done")

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


def test_a_tool_done_becomes_a_duration_not_a_second_step(tmp_path) -> None:
    """The timing hook reports under the step's tool_use_id. Matched, it is the
    step's duration; unmatched, it is a subagent's call — the message stream
    only carries the parent's, so this is how subagent work reaches the feed."""

    async def scenario() -> None:
        engine = FakeEngine(
            events=[
                {"type": "tool_use", "id": "tu_1", "name": "Bash", "detail": "df -h"},
                {"type": "tool_done", "id": "tu_1", "name": "Bash", "detail": "df -h", "ms": 340},
                {"type": "tool_done", "id": "tu_sub", "name": "Grep", "detail": "error", "ms": 12},
                {"type": "tool_done", "id": "tu_gone", "name": "Bash", "detail": "curl x", "ms": 900, "error": True},
            ]
        )
        service = RunService(make_settings(tmp_path), engine, RunStore(tmp_path / "results"))
        service.start({"message": "analyze", "sessionKey": "hook:durations"})
        done = await _wait_finished(service, "hook:durations")

        events = done.turns[0]["events"]
        steps = [e for e in events if e.get("type") == "tool_use"]
        assert len(steps) == 3, "two subagent calls appended, no duplicate for the matched one"
        assert steps[0]["ms"] == 340 and "sub" not in steps[0]
        assert steps[1]["sub"] is True
        assert steps[1]["name"] == "Grep" and steps[1]["ms"] == 12
        assert steps[2]["sub"] is True and steps[2]["error"] is True

    asyncio.run(scenario())


# -- the interrupt: a stop that still knows what it cost ----------------------


def test_an_operator_stop_records_what_the_turn_spent(tmp_path) -> None:
    """The Stop button was the most frequent way a bill went missing.

    It cancelled the coroutine, which discarded the SDK's final message and with
    it the cost — so every stop recorded None, "nobody counted", for a run the
    provider had already billed in full. Timeouts were the rare case; this is the
    one an operator presses on purpose.
    """

    async def scenario() -> None:
        engine = FakeEngine(delay=30.0)
        service = RunService(make_settings(tmp_path), engine, RunStore(tmp_path / "results"))
        service.start({"message": "analyze", "sessionKey": "hook:stop"})
        await asyncio.sleep(0.05)

        service.stop("hook:stop")
        done = await _wait_finished(service, "hook:stop", deadline=5.0)

        assert engine.interrupts == 1, "the turn was asked to stop, not killed"
        assert done.finished
        assert done.turns[-1]["cost_usd"] == engine.interrupted_cost, "a stop must still know its bill"
        assert service.window_unpriced() == 0

    asyncio.run(scenario())


def test_an_sdk_that_ignores_the_interrupt_is_still_stopped(tmp_path) -> None:
    """The fallback, and why it cannot be dropped: an interrupt is a request. A
    turn that has not reached the SDK yet has nothing to interrupt, and one the
    SDK ignores must not run forever because we asked politely."""

    async def scenario() -> None:
        engine = FakeEngine(delay=30.0)
        engine.stoppable = False  # the SDK declines
        service = RunService(make_settings(tmp_path), engine, RunStore(tmp_path / "results"))
        service.start({"message": "analyze", "sessionKey": "hook:stubborn"})
        await asyncio.sleep(0.05)

        service.stop("hook:stubborn")
        done = await _wait_finished(service, "hook:stubborn", deadline=5.0)

        assert engine.interrupts == 1, "it was asked first"
        assert done.finished, "and cancelled when the ask was declined"
        assert done.error is not None and "stopped by operator" in done.error
        # Nobody counted this one, and the ledger says so rather than saying zero.
        assert done.turns[-1]["cost_usd"] is None
        assert service.window_unpriced() == 1

    asyncio.run(scenario())


def test_a_restart_asks_the_turns_in_flight_to_wind_down(tmp_path) -> None:
    """This path runs on every deploy, so it was the most frequent of the three
    that threw a bill away — a restart during three investigations lost three
    costs, every time."""

    async def scenario() -> None:
        engine = FakeEngine(delay=30.0)
        service = RunService(make_settings(tmp_path), engine, RunStore(tmp_path / "results"))
        service.start({"message": "analyze", "sessionKey": "hook:deploying"})
        await asyncio.sleep(0.05)

        await service.shutdown(grace_seconds=5.0)

        assert engine.interrupts >= 1, "a deploy asks before it kills"

    asyncio.run(scenario())


class _Result:
    """The shape the SDK hands back — only the fields the decision reads."""

    def __init__(self, *, is_error: bool, subtype: str) -> None:
        self.is_error = is_error
        self.subtype = subtype


def test_a_failed_run_reports_what_the_engine_said_not_its_subtype() -> None:
    """Production said `engine reported success` about a run that failed.

    The first real patrol run died five seconds in. The engine had said `API
    Error: 402 Insufficient Balance` — an unambiguous, immediately actionable
    reason — and the operator was handed `engine reported success`, because the
    message was built from `subtype`, which the SDK had set to "success" on a
    result it had already flagged `is_error`.

    A contradiction on its face, no information in it, and the real reason one
    field away. What reaches the log line and `reason=` on the board has to be
    the thing that happened.
    """
    failed = _Result(is_error=True, subtype="success")
    assert engine_error(failed, "API Error: 402 Insufficient Balance") == "API Error: 402 Insufficient Balance"

    # Collapsed to one line and capped: this is a log line, not a report.
    long = engine_error(failed, "line one\n\n   line two" + " x" * 300)
    assert long is not None and "\n" not in long and long.startswith("line one line two")
    assert len(long) == 200

    # The subtype is still the fallback, for an error that came with no words.
    assert engine_error(_Result(is_error=True, subtype="mystery"), "  ") == "engine reported mystery"

    # But a CUTOFF is the exception to preferring the text, because there the
    # text is the model's last sentence mid-thought rather than a fault it
    # reported. A live run put `reason=That prior analysis is rich. Now I need
    # to place the current alert…` on the board — neither an error nor an
    # answer. The limit that stopped it is the actionable fact.
    cut = engine_error(
        _Result(is_error=True, subtype="error_max_turns"),
        "That prior analysis is rich. Now I need to place the current alert",
    )
    assert cut == "stopped at the turn limit before reaching a conclusion (error_max_turns)"
    assert "prior analysis" not in cut

    tokens = engine_error(_Result(is_error=True, subtype="error_max_tokens"), "and then I would")
    assert tokens is not None and tokens.startswith("stopped at the output limit")

    # And a genuine success stays a success; an empty one is still a failure.
    assert engine_error(_Result(is_error=False, subtype="success"), "the report") is None
    assert engine_error(_Result(is_error=False, subtype="success"), "") == "engine returned an empty result"
