"""How full the context was, and when the runtime folded it away.

A patrol on this deployment died on "the model has reached its context window
limit" and nothing anywhere had said it was close. The number existed the whole
time; nobody asked for it. These pin the asking, and the honest shape of the
answer when the runtime does not give one.
"""

from __future__ import annotations

import asyncio
from typing import Any

from hookprobe.engine import EngineResult, _compaction_hook, _context_facts
from hookprobe.runs import RunStore
from hookprobe.service import RunService
from tests.helpers import FakeEngine, make_settings


class _Usage:
    def __init__(self, **kw: Any) -> None:
        self.__dict__.update(kw)


def test_only_the_facts_that_keep_their_shape_are_recorded() -> None:
    facts = _context_facts(_Usage(totalTokens=90_000, maxTokens=200_000, percentage=45.04, isAutoCompactEnabled=True))
    assert facts == {"tokens": 90_000, "limit": 200_000, "percent": 45.0, "auto_compact": True}

    # `categories` is a per-kind breakdown whose shape follows the runtime; a
    # record is worth more when it holds the same keys next year.
    rich = _context_facts(_Usage(totalTokens=1, maxTokens=2, percentage=50.0, categories={"tools": 1}))
    assert "categories" not in rich

    # A runtime that answers nothing gets None, not zeros — "we were not told"
    # and "the context was empty" are different facts.
    assert _context_facts(_Usage()) is None
    assert _context_facts(_Usage(totalTokens=None, maxTokens=None)) is None


def test_a_compaction_is_recorded_with_what_triggered_it() -> None:
    seen: list[dict[str, Any]] = []
    hook = _compaction_hook(seen.append)
    asyncio.run(hook({"trigger": "auto", "custom_instructions": "keep the evidence"}, None, None))
    assert seen == [{"type": "compacted", "trigger": "auto", "instructions": "keep the evidence"}]

    # Nothing is refused here: it is an observation, not a gate.
    assert asyncio.run(hook({}, None, None)) == {}


def test_the_turn_record_keeps_both(tmp_path) -> None:
    engine = FakeEngine(
        result=EngineResult(
            text='{"summary": "ok"}',
            message_count=2,
            cost_usd=0.4,
            session_id="s1",
            context={"tokens": 180_000, "limit": 200_000, "percent": 90.0, "auto_compact": False},
            compactions=({"type": "compacted", "trigger": "auto"},),
        )
    )
    service = RunService(make_settings(tmp_path), engine, RunStore(tmp_path / "results"))

    async def scenario() -> None:
        service.start({"message": "look", "sessionKey": "probe:inbound:1"})
        for _ in range(300):
            run = service.get("probe:inbound:1")
            if run and run.finished:
                break
            await asyncio.sleep(0.01)
        run = service.get("probe:inbound:1")
        assert run.turns[0]["context"]["percent"] == 90.0
        assert run.turns[0]["compactions"] == [{"type": "compacted", "trigger": "auto"}]

    asyncio.run(scenario())


def test_a_runtime_that_says_nothing_leaves_the_record_saying_nothing(tmp_path) -> None:
    service = RunService(make_settings(tmp_path), FakeEngine(), RunStore(tmp_path / "results"))

    async def scenario() -> None:
        service.start({"message": "look", "sessionKey": "probe:inbound:2"})
        for _ in range(300):
            run = service.get("probe:inbound:2")
            if run and run.finished:
                break
            await asyncio.sleep(0.01)
        run = service.get("probe:inbound:2")
        assert run.turns[0]["context"] is None and run.turns[0]["compactions"] == []

    asyncio.run(scenario())
