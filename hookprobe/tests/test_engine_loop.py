"""The engine's receive loop — the one expensive thing nothing was watching.

Every other test here injects a FakeEngine at the `Engine` protocol boundary, so
the SDK never has to be installed to run them. That is a good rule and this file
does not break it: the loop under test is reached with REAL message objects and a
substituted client, so nothing here talks to a model, spends anything, or needs a
credential.

What it does need is the SDK's message classes, which are a declared dependency
and sit in the same venv as the service. The rule was "tests never import the
SDK"; taken literally it left `run()` outside the suite entirely — and inside
`run()` are the cost accounting, the interrupt path, the redaction capture point
and the input fingerprint diff. Two bugs shipped from it in one day:

  * a failed run reported `engine reported success`, because the message was
    built from `subtype` on a result already flagged is_error;
  * `messageCount` on /final counted StreamEvent deltas, so one investigation
    reported 32,294 and the service logged it as `turns=32294`.

Both were found by running the thing on production and reading the output. Both
were one assertion away from being found here.

`from claude_agent_sdk import X` inside `run()` resolves `X` on the module at CALL
time, which is what makes the client substitutable without touching the engine.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import claude_agent_sdk
import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, StreamEvent, TextBlock, ToolUseBlock

from hookprobe.engine import ClaudeAgentEngine
from tests.helpers import make_settings


def _delta(text: str) -> StreamEvent:
    """One token-level delta, the kind `include_partial_messages=True` produces."""
    return StreamEvent(
        uuid="u",
        session_id="s",
        event={"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}},
    )


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


class _FakeClient:
    """Stands in for ClaudeSDKClient: scripted messages, no subprocess.

    Records connect/disconnect so the finally-block contract is checked too — a
    client left open holds the CLI subprocess, which is why disconnect() is not
    optional.
    """

    scripted: list[Any] = []
    connected = 0
    disconnected = 0
    queries: list[str] = []

    def __init__(self, options: Any = None) -> None:
        self.options = options

    async def connect(self) -> None:
        type(self).connected += 1

    async def disconnect(self) -> None:
        type(self).disconnected += 1

    async def query(self, message: str) -> None:
        type(self).queries.append(message)

    async def interrupt(self) -> None:  # pragma: no cover — the stop path has its own tests
        return None

    async def receive_response(self):
        for message in type(self).scripted:
            yield message


@pytest.fixture
def engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """An engine whose client is substituted, and a clean recording slate."""
    _FakeClient.connected = 0
    _FakeClient.disconnected = 0
    _FakeClient.queries = []
    _FakeClient.scripted = []
    monkeypatch.setattr(claude_agent_sdk, "ClaudeSDKClient", _FakeClient)
    settings = make_settings(tmp_path, workdir=tmp_path)
    return ClaudeAgentEngine(settings)


def _run(engine: ClaudeAgentEngine, messages: list[Any]):
    _FakeClient.scripted = messages
    return asyncio.run(engine.run(message="investigate", session_key="probe:t:1"))


def test_deltas_are_streamed_but_not_counted(engine) -> None:
    """The 32,294 bug, as an assertion.

    `include_partial_messages=True` makes the SDK emit one StreamEvent per
    token-level delta. Counting them turned `messageCount` — a public field the
    platform polls — into a token counter, and the service logged it as `turns`,
    a word under which 32,294 is absurd on its face rather than merely large.

    Deltas still reach a watcher: they are what makes the console live. They are
    just not messages.
    """
    seen: list[dict[str, Any]] = []
    _FakeClient.scripted = [
        _delta("thinking "),
        _delta("out "),
        _delta("loud"),
        AssistantMessage(content=[TextBlock(text="the report")], model="claude-opus-5"),
        _result(),
    ]
    result = asyncio.run(engine.run(message="investigate", session_key="probe:t:1", on_event=seen.append))

    assert result.message_count == 2, "one assistant message and one result; three deltas are not messages"
    assert [e["text"] for e in seen if e["type"] == "delta"] == ["thinking ", "out ", "loud"]
    assert result.text == "the report"


def test_a_failed_result_reports_what_the_engine_said(engine) -> None:
    """The `engine reported success` bug, as an assertion.

    The SDK can set `is_error` on a result whose `subtype` reads "success". The
    operator got the subtype: a sentence with no information and a contradiction
    on its face, while the actual reason sat in `result`.
    """
    result = _run(
        engine,
        [
            AssistantMessage(content=[TextBlock(text="API Error: 402 Insufficient Balance")], model="m"),
            _result(is_error=True, subtype="success", result="API Error: 402 Insufficient Balance"),
        ],
    )

    assert result.error == "API Error: 402 Insufficient Balance"
    assert "success" not in (result.error or ""), "the subtype is not the reason"


def test_a_failure_with_no_words_falls_back_to_the_subtype(engine) -> None:
    """Which is the only case the subtype was ever any use for — bar the
    cutoffs below, where it is the whole answer."""
    result = _run(engine, [_result(is_error=True, subtype="provider_hiccup", result="")])
    assert result.error == "engine reported provider_hiccup"


def test_a_run_cut_off_at_its_limit_names_the_limit_not_the_last_sentence(engine) -> None:
    """A live run reported `reason=That prior analysis is rich. Now I need to
    place the current alert…` — the model's narration mid-thought, presented as
    a failure. On a cutoff the text is not a fault the engine reported, and the
    limit is what an operator can act on."""
    result = _run(
        engine,
        [_result(is_error=True, subtype="error_max_turns", result="Now I need to place the current alert")],
    )
    assert result.error == "stopped at the turn limit before reaching a conclusion (error_max_turns)"


def test_cost_and_session_survive_a_result(engine) -> None:
    """The reason the engine uses ClaudeSDKClient instead of query(): the result
    message arrives, so the bill and the resumable session id are recorded rather
    than discarded with a cancelled generator."""
    result = _run(engine, [AssistantMessage(content=[TextBlock(text="ok")], model="m"), _result()])

    assert result.cost_usd == 0.25
    assert result.session_id == "engine-session-1"
    assert result.duration_ms == 1200
    assert result.usage == {"input_tokens": 10, "output_tokens": 20}


def test_no_result_message_is_an_error_not_a_silent_success(engine) -> None:
    """A stream that ends without a result has produced no answer, and the run
    must not settle as though it had."""
    result = _run(engine, [AssistantMessage(content=[TextBlock(text="partial")], model="m")])
    assert result.error == "engine produced no result message"
    assert result.text == "partial", "whatever was said is still kept"


def test_an_empty_answer_is_a_failure(engine) -> None:
    result = _run(engine, [_result(result="")])
    assert result.error == "engine returned an empty result"


def test_the_client_is_always_disconnected(engine) -> None:
    """In a finally, because a client left open holds the CLI subprocess. Asserted
    on the failure path too — the path where forgetting is easy."""
    _run(engine, [_result(is_error=True, subtype="success", result="boom")])
    assert _FakeClient.connected == 1 and _FakeClient.disconnected == 1


def test_a_tool_call_is_recorded_with_its_detail_redacted(engine) -> None:
    """The redaction capture point is inside this loop, which is why all three
    sinks inherit it — and why nothing outside this loop could have tested it."""
    seen: list[dict[str, Any]] = []
    command = "curl -H 'Authorization: Bearer sk-abcdef123456' https://api.internal/x"
    _FakeClient.scripted = [
        AssistantMessage(content=[ToolUseBlock(id="t1", name="Bash", input={"command": command})], model="m"),
        _result(),
    ]
    asyncio.run(engine.run(message="investigate", session_key="probe:t:1", on_event=seen.append))

    tools = [e for e in seen if e.get("type") == "tool_use"]
    assert tools, "the tool call reached the feed"
    detail = str(tools[0].get("detail") or "")
    assert "sk-abcdef123456" not in detail, f"the credential rode along: {detail}"
