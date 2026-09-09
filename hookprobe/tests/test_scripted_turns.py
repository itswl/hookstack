"""How many times one run may talk to the runtime, and what happens if it talks more.

`ScriptedTurns` (tests/helpers.py) is borrowed in shape from
`agents.testing.ScriptedModel` in openai/openai-agents-python. The property
this repository did not have: a test double that FAILS when the loop makes a
call nobody scripted, and when scripted steps go unconsumed.

That matters here more than it would elsewhere. The product's central argument
is cost, `FakeEngine` sits above the receive loop and never enters it, and the
loop's own fake replayed a single stream — so "did this turn ask the runtime
twice" was a question no test in this suite could answer. A turn asked twice is
a turn billed twice.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import claude_agent_sdk
import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

from hookprobe.engine import ClaudeAgentEngine
from tests.helpers import ScriptedTurns, UnscriptedCall, make_settings


def _assistant(text: str) -> AssistantMessage:
    return AssistantMessage(content=[TextBlock(text=text)], model="m")


def _result(**over: Any) -> ResultMessage:
    base: dict[str, Any] = {
        "subtype": "success",
        "duration_ms": 1200,
        "duration_api_ms": 900,
        "is_error": False,
        "num_turns": 1,
        "session_id": "engine-session-1",
        "total_cost_usd": 0.25,
        "result": "the report",
        "usage": {"input_tokens": 10, "output_tokens": 20},
    }
    return ResultMessage(**{**base, **over})


def _engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: ScriptedTurns) -> ClaudeAgentEngine:
    monkeypatch.setattr(claude_agent_sdk, "ClaudeSDKClient", client.factory())
    return ClaudeAgentEngine(make_settings(tmp_path, workdir=tmp_path))


def test_one_run_is_exactly_one_turn_on_the_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The assertion the old fake could not make. Everything else in this file
    exists to prove this one is load-bearing."""
    client = ScriptedTurns([[_assistant("ok"), _result()]])
    engine = _engine(tmp_path, monkeypatch, client)
    result = asyncio.run(engine.run(message="investigate", session_key="probe:t:1"))

    assert result.cost_usd == 0.25
    assert client.queries == ["investigate"], "one run, one query — a second would be a second bill"
    assert client.connects == 1 and client.disconnects == 1
    client.assert_consumed()


def test_an_unscripted_turn_is_an_error_and_not_a_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Proving the instrument reads, the way assert_node_contract.py does: a
    double that cannot fail is a double that will let the real defect through."""
    client = ScriptedTurns([[_assistant("ok"), _result()]])
    engine = _engine(tmp_path, monkeypatch, client)
    asyncio.run(engine.run(message="first", session_key="probe:t:1"))

    with pytest.raises(UnscriptedCall, match="nobody budgeted"):
        asyncio.run(engine.run(message="second", session_key="probe:t:1"))


def test_unconsumed_turns_are_an_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The other half: a loop that stops earlier than the test expected."""
    client = ScriptedTurns([[_assistant("ok"), _result()], [_assistant("never reached"), _result()]])
    engine = _engine(tmp_path, monkeypatch, client)
    asyncio.run(engine.run(message="only one", session_key="probe:t:1"))

    with pytest.raises(UnscriptedCall, match="unconsumed"):
        client.assert_consumed()


def test_the_context_probe_is_asked_once_per_process_not_once_per_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The extra round trip on the hot path, counted at the client rather than
    inferred. The first version of that probe cost ten seconds on EVERY turn
    against a CLI that never answers it; the latch is per process, so three
    runs on one engine must ask exactly once.
    """
    client = ScriptedTurns([[_assistant("ok"), _result()] for _ in range(3)])
    engine = _engine(tmp_path, monkeypatch, client)
    for i in range(3):
        asyncio.run(engine.run(message="m", session_key=f"probe:t:{i}"))

    assert client.context_usage_asks == 1, "asked once, learned it does not answer, stopped asking"
    client.assert_consumed()


def test_the_client_is_disconnected_even_when_the_turn_blows_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A client left open holds the CLI subprocess, so the finally-block is not
    optional — and with the calls recorded, the ORDER can be checked too."""
    client = ScriptedTurns([[_assistant("ok"), _result(is_error=True, subtype="error_during_execution")]])
    engine = _engine(tmp_path, monkeypatch, client)
    result = asyncio.run(engine.run(message="m", session_key="probe:t:1"))

    assert result.error, "a failed turn is still a turn that reports"
    assert client.calls[0] == "connect" and client.calls[-1] == "disconnect"


def test_the_context_probe_can_now_actually_answer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Before the reorder this was impossible, on any runtime.

    `_context_usage(client)` was called AFTER the finally that disconnects, so
    the probe always hit a closed client. It failed every time, the per-process
    latch recorded "this runtime does not report context usage", and every run
    reported `context: null` — a conclusion about the runtime drawn from our own
    call order. The reorder is the fix; this asserts the number arrives.
    """

    class _Usage:
        totalTokens = 41_000
        maxTokens = 200_000
        percentage = 20.5
        isAutoCompactEnabled = True

    client = ScriptedTurns([[_assistant("ok"), _result()]], context_usage=_Usage())
    engine = _engine(tmp_path, monkeypatch, client)
    result = asyncio.run(engine.run(message="m", session_key="probe:t:1"))

    assert result.context == {"tokens": 41_000, "limit": 200_000, "percent": 20.5, "auto_compact": True}
    assert client.context_usage_asks == 1
    # And the ordering that makes it possible, pinned so it cannot drift back.
    assert client.calls.index("get_context_usage") < client.calls.index("disconnect")
    client.assert_consumed()
