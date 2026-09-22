"""A failure that is about the node, not the question, says so out loud.

The gateway 404'd for 33 hours (2026-09-21) and 403'd for ~35 minutes
(2026-09-22). The hourly selftest added after the first outage missed the
second completely: it began after one tick and was fixed before the next.
Polling an hourly question can only find an outage longer than an hour. The
runs are the dense signal — they fail the moment the thing breaks.
"""

from __future__ import annotations

import asyncio

import pytest

from hookprobe.engine import EngineResult, unreachable
from hookprobe.runs import RunStore
from hookprobe.service import RunService
from tests.helpers import FakeEngine, make_settings


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        ("Failed to authenticate. API Error: 403 Forbidden", "refusing this node"),
        ("API Error: 401 Unauthorized", "rejecting this node's key"),
        ("API Error: 402 Insufficient Balance", "cannot pay"),
        ("connection error: dial tcp 10.0.0.1:443", "unreachable from this node"),
        ("API Error: 404 page not found", "not at the endpoint or model"),
    ],
)
def test_the_shapes_that_mean_every_run_here_would_fail(error: str, expected: str) -> None:
    why = unreachable(error)
    assert why is not None and expected in why


@pytest.mark.parametrize(
    "error",
    [
        "API Error: 524",  # a gateway timeout: retried, not paged
        "cancelled during shutdown",  # our own restart
        "interrupted by a restart",
        "context window exceeded",  # about this prompt, not this node
        "the model declined to answer",
        "",
    ],
)
def test_one_investigation_dying_is_not_an_alarm(error: str) -> None:
    """The narrow list is the point. An operator woken for a single run's
    timeout learns to ignore the channel, which is how an alarm stops working."""
    assert unreachable(error) is None


def _run_failing_with(tmp_path, error: str, *, alarm_url: str = "https://alarm.invalid/hook"):
    settings = make_settings(tmp_path, workdir=tmp_path, alarm_url=alarm_url)
    engine = FakeEngine(result=EngineResult(text="", message_count=1, error=error))
    service = RunService(settings, engine, RunStore(tmp_path / "results"))
    sent: list[str] = []

    async def fake_alarm(text: str) -> bool:
        sent.append(text)
        return True

    service.alarm = fake_alarm  # type: ignore[method-assign]

    async def scenario():
        service.start({"message": "Title: t\ngo", "sessionKey": "probe:alert:1"})
        for _ in range(400):
            run = service.get("probe:alert:1")
            if run and run.finished and ("alarm" in run.meta or run.meta.get("node_wide_failure") is None):
                await asyncio.sleep(0.05)  # let the detached alarm task land
                return run
            await asyncio.sleep(0.01)
        raise AssertionError("never finished")

    return asyncio.run(scenario()), sent


def test_a_gateway_refusal_pages_once_with_what_to_do(tmp_path) -> None:
    run, sent = _run_failing_with(tmp_path, "Failed to authenticate. API Error: 403 Forbidden")
    assert run.meta["node_wide_failure"] == "the gateway is refusing this node"
    assert run.meta["alarm"] == "sent"
    assert len(sent) == 1
    assert "probe:alert:1" in sent[0] and "403" in sent[0]
    assert "every investigation this node runs would fail" in sent[0]


def test_a_node_with_no_channel_records_that_it_could_not_say_so(tmp_path) -> None:
    """`no channel` on the run itself: a node failing silently is a state an
    operator has to be able to SEE, which is why the selftest watch records the
    same three words."""
    run, sent = _run_failing_with(tmp_path, "API Error: 403 Forbidden", alarm_url="")
    assert run.meta["node_wide_failure"] and run.meta["alarm"] == "no channel"
    assert sent == []


def test_an_ordinary_failure_pages_nobody(tmp_path) -> None:
    run, sent = _run_failing_with(tmp_path, "context window exceeded")
    assert "node_wide_failure" not in run.meta and "alarm" not in run.meta
    assert sent == []
