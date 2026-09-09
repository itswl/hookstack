"""Cost as arithmetic over two measured things, instead of somebody else's table.

`cost_usd` used to be whatever the runtime felt like reporting: the Claude CLI
prices from its own table for a model it is not the one billing, codex reports
tokens and never money, and pi priced from a catalogue this deployment's model
is absent from. Two of those are estimates for the wrong model and one is
nothing at all — and nothing is what left a budget ceiling unable to bind.

Tokens every runtime reports and none has to guess. Times the operator's own
stated rate for their own gateway ($0.20 in / $0.02 cache read / $1.20 out per
million, stated 2026-09-09), that is the first cost figure here that is
arithmetic rather than a table somebody else maintains. Still PRICED and not
BILLED — a rate goes stale and a gateway can charge for something four numbers
do not name — but priced from the right table.
"""

from __future__ import annotations

from pathlib import Path

from hookprobe.engine import price_tokens

# The operator's rates, per million.
RATES = (0.20, 0.02, 0.20, 1.20)


def test_the_stated_rates_price_a_real_turn() -> None:
    """Numbers from an actual turn on this deployment: 93,388 fresh input
    tokens across three codex turns, which reported no money at all."""
    usage = {
        "input_tokens": 93_388,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "output_tokens": 1_200,
    }
    # 93388 * 0.20 / 1e6 + 1200 * 1.20 / 1e6
    assert price_tokens(usage, RATES) == round((93_388 * 0.20 + 1_200 * 1.20) / 1_000_000, 6)
    assert price_tokens(usage, RATES) == 0.020118


def test_a_cache_read_is_a_tenth_of_fresh_input_and_the_arithmetic_says_so() -> None:
    """The reason the cache rate is worth carrying: reads are $0.02 against
    $0.20, so pricing them as input over-states a cached turn tenfold."""
    cached = {"input_tokens": 0, "cache_read_input_tokens": 1_000_000, "output_tokens": 0}
    fresh = {"input_tokens": 1_000_000, "cache_read_input_tokens": 0, "output_tokens": 0}
    assert price_tokens(cached, RATES) == 0.02
    assert price_tokens(fresh, RATES) == 0.20


def test_writes_are_charged_at_the_input_rate_because_nobody_stated_one() -> None:
    """Three rates were given and a write rate was not among them. Writes cost
    at least fresh input on every provider that charges for them, so that is
    the default — and it is a knob, so a fourth number corrects it without a
    code change. A write charged at nothing would be the zero-with-tokens lie
    again, one layer up."""
    written = {"input_tokens": 0, "cache_creation_input_tokens": 1_000_000, "output_tokens": 0}
    assert price_tokens(written, RATES) == 0.20


def test_no_rates_means_no_opinion(tmp_path: Path) -> None:
    """Every deployment until somebody sets them. Silence is how this stays a
    change nobody acquires by upgrading."""
    usage = {"input_tokens": 1_000, "output_tokens": 100}
    assert price_tokens(usage, (0.0, 0.0, 0.0, 0.0)) is None
    # And a turn with rates but no tokens is unpriced rather than free — the
    # same rule `priced()` holds one layer down.
    assert price_tokens({"input_tokens": 0, "output_tokens": 0}, RATES) is None
    assert price_tokens(None, RATES) is None


def test_the_service_prices_every_runtime_the_same_way(tmp_path: Path) -> None:
    """The seam is in the service and not in each engine, so the runtime that
    reports no money gets the same number as the one that reports the wrong
    money — which is what un-blinds the ceiling on codex and pi."""
    from hookprobe.engine import EngineResult
    from hookprobe.runs import RunStore
    from hookprobe.service import RunService
    from tests.helpers import FakeEngine, make_settings

    usage = {"input_tokens": 100_000, "cache_read_input_tokens": 50_000, "output_tokens": 2_000}
    expected = round((100_000 * 0.20 + 50_000 * 0.02 + 2_000 * 1.20) / 1_000_000, 6)

    priced = make_settings(
        tmp_path,
        price_in_per_1m=0.20,
        price_cache_read_per_1m=0.02,
        price_cache_write_per_1m=0.20,
        price_out_per_1m=1.20,
    )
    service = RunService(priced, FakeEngine(), RunStore(tmp_path / "results"))
    # codex: tokens, and deliberately no money at all.
    assert service._turn_cost(EngineResult(text="t", usage=usage, cost_usd=None)) == expected
    # claude: the CLI's estimate for a model it is not billing, overridden.
    assert service._turn_cost(EngineResult(text="t", usage=usage, cost_usd=9.99)) == expected

    # Unconfigured, the runtime's own number survives untouched.
    plain = RunService(make_settings(tmp_path), FakeEngine(), RunStore(tmp_path / "results2"))
    assert plain._turn_cost(EngineResult(text="t", usage=usage, cost_usd=9.99)) == 9.99
    assert plain._turn_cost(EngineResult(text="t", usage=usage, cost_usd=None)) is None
