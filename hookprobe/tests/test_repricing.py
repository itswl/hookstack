"""A node that declares its rates prices every recorded turn from its tokens.

2026-09-23: the local work stack had run for weeks with HOOKPROBE_PRICE_*
empty, so every turn carried the Claude CLI's list price for a model it was not
the one billing — 30 to 60 times the gateway's rate. Declaring the rates fixed
the NEXT turn only. The budget window kept adding up the old stored dollars, so
lowering a ceiling to its real size the same day would have tripped the breaker
on money that was never spent; and the run list, the run page, the waterfall and
the work board would each have shown a different number for the same turn.

The fix is one function, runs.turn_cost, that every reader goes through. These
tests reproduce the real sequence: a turn recorded on a node with no rates, then
read back by the same node once it declares them.
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from hookprobe import telemetry, work
from hookprobe.app import create_app
from hookprobe.engine import EngineResult
from hookprobe.runs import Run, RunStore, turn_cost
from hookprobe.service import RunService
from tests.helpers import FakeEngine, make_settings

TOKEN = "secret-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
# What production's node actually prices at. Its cache-write knob is empty and
# settings.py resolves an empty one to the input rate; make_settings builds the
# dataclass directly and would otherwise leave it at 0, so it is stated here.
RATES = {
    "price_in_per_1m": 0.20,
    "price_cache_read_per_1m": 0.02,
    "price_cache_write_per_1m": 0.20,
    "price_out_per_1m": 1.20,
}
# One real turn off the local planner (SRE-28, 2026-09-23), recorded at $2.50.
USAGE = {
    "input_tokens": 4_055,
    "cache_read_input_tokens": 1_096_044,
    "cache_creation_input_tokens": 219_551,
    "output_tokens": 11_467,
}
RECORDED = 2.50
# Cache writes unset -> the input rate (settings.py), as on production.
PRICED = round((4_055 * 0.20 + 1_096_044 * 0.02 + 219_551 * 0.20 + 11_467 * 1.20) / 1_000_000, 6)


def _recorded_without_rates(tmp_path) -> None:
    """A turn written by a node that declared no rates: the CLI's figure stored."""
    settings = make_settings(tmp_path)
    engine = FakeEngine(result=EngineResult(text="done", message_count=1, usage=USAGE, cost_usd=RECORDED))
    service = RunService(settings, engine, RunStore(tmp_path / "results"))

    async def scenario():
        service.start({"message": "Title: t\ngo", "sessionKey": "probe:watch:580"})
        for _ in range(300):
            run = service.get("probe:watch:580")
            if run and run.finished:
                return
            await asyncio.sleep(0.01)
        raise AssertionError("never finished")

    asyncio.run(scenario())


def _node(tmp_path, **rates):
    settings = make_settings(tmp_path, token=TOKEN, **rates)
    service = RunService(settings, FakeEngine(), RunStore(tmp_path / "results"))
    return settings, service


def test_the_breaker_counts_old_turns_at_the_rates_declared_now(tmp_path) -> None:
    _recorded_without_rates(tmp_path)
    _, unpriced = _node(tmp_path)
    assert abs(unpriced.window_spend() - RECORDED) < 1e-6, "no rates: the recorded figure, as before"
    _, priced = _node(tmp_path, **RATES)
    assert abs(priced.window_spend() - PRICED) < 1e-6, "rates declared: the tokens, not the stored dollars"
    assert PRICED < RECORDED / 25, "the gap this exists for"


def test_a_turn_without_usage_keeps_its_recorded_figure() -> None:
    """A runtime that reports dollars and no tokens is still counted."""
    price = lambda usage: None if not usage else 1.0  # noqa: E731
    assert turn_cost({"cost_usd": 0.3}, price) == 0.3
    assert turn_cost({"cost_usd": 0.3, "usage": {"input_tokens": 1}}, price) == 1.0
    assert turn_cost({"cost_usd": 0.3}, None) == 0.3
    assert turn_cost({}, None) is None


def test_every_page_shows_the_number_the_breaker_counts(tmp_path) -> None:
    """The list, its spend bars, the run page and the budget agree, and a
    repriced figure carries the recorded one beside it."""
    _recorded_without_rates(tmp_path)
    settings, service = _node(tmp_path, **RATES)
    client = TestClient(create_app(settings, service))

    budget = client.get("/v1/budget", headers=AUTH).json()
    assert abs(budget["spent_usd"] - PRICED) < 1e-6
    assert budget["priced_by"] == "declared rates"

    (row,) = client.get("/v1/runs", headers=AUTH).json()
    assert abs(row["cost_usd"] - PRICED) < 1e-6 and row["recorded_cost_usd"] == RECORDED

    page = client.get("/v1/runs/probe:watch:580", headers=AUTH).json()
    (turn,) = page["turns"]
    assert abs(turn["cost_usd"] - PRICED) < 1e-6 and turn["recorded_cost_usd"] == RECORDED
    assert abs(page["cost_usd"] - PRICED) < 1e-6 and page["recorded_cost_usd"] == RECORDED

    (item,) = client.get("/v1/work", headers=AUTH).json()["items"]
    assert abs(item["cost_usd"] - PRICED) < 1e-6


def test_a_node_without_rates_reads_exactly_as_before(tmp_path) -> None:
    _recorded_without_rates(tmp_path)
    settings, service = _node(tmp_path)
    client = TestClient(create_app(settings, service))
    assert client.get("/v1/budget", headers=AUTH).json()["priced_by"] == "runtime estimate"
    (row,) = client.get("/v1/runs", headers=AUTH).json()
    assert row["cost_usd"] == RECORDED and "recorded_cost_usd" not in row
    (turn,) = client.get("/v1/runs/probe:watch:580", headers=AUTH).json()["turns"]
    assert turn["cost_usd"] == RECORDED and "recorded_cost_usd" not in turn


def test_the_waterfall_prices_each_call_from_its_tokens() -> None:
    """The runtime's per-call figure is its list price too; with rates declared,
    "costliest: #1 $0.45" cannot sit under a header that says $0.03."""
    lines = [
        {
            "kind": "event",
            "name": "api_request",
            "ts": 1_790_000_000.0,
            "attrs": {
                "model": "gpt-5.6-luna",
                "duration_ms": 1800,
                "cost_usd": 0.45,
                "input_tokens": 2_000,
                "output_tokens": 500,
                "cache_read_tokens": 100_000,
                "cache_creation_tokens": 10_000,
            },
        }
    ]
    rates = (0.20, 0.02, 0.20, 1.20)
    from hookprobe.engine import price_tokens

    shape = telemetry.summarize(lines, lambda usage: price_tokens(usage, rates))
    (call,) = [i for i in shape["waterfall"] if i["kind"] == "model"]
    expected = round((2_000 * 0.20 + 100_000 * 0.02 + 10_000 * 0.20 + 500 * 1.20) / 1_000_000, 6)
    assert abs(call["cost_usd"] - expected) < 1e-9 and call["recorded_cost_usd"] == 0.45
    assert abs(shape["summary"]["cost_usd"] - expected) < 1e-6
    # Without rates the runtime's figure is shown, unlabelled, as before.
    plain = telemetry.summarize(lines)
    (call,) = [i for i in plain["waterfall"] if i["kind"] == "model"]
    assert call["cost_usd"] == 0.45 and "recorded_cost_usd" not in call


def test_the_work_board_prices_a_job_like_the_budget_does() -> None:
    run = Run(session_key="probe:plan-approved:9", run_id="r9")
    run.text = "done"
    run.turns = [{"finished_at": 1.0, "cost_usd": RECORDED, "usage": USAGE}]
    rates = (0.20, 0.02, 0.20, 1.20)
    from hookprobe.engine import price_tokens

    (item,) = work.resolve([run], price=lambda usage: price_tokens(usage, rates))
    assert abs(item.cost_usd - PRICED) < 1e-6
    (plain,) = work.resolve([run])
    assert plain.cost_usd == RECORDED
