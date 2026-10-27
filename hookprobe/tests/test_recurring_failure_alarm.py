"""A transient failure that keeps happening is news; one that happens once is not.

A second deployment running copies of these files lost three days of rounds to a
provider's rate limit with nobody told. Here a rate-limited run was retried,
failed again, and was filed as that one investigation's bad luck, every time.
"""

from __future__ import annotations

import asyncio

from hookprobe import engine as engine_module
from hookprobe import service as service_module
from hookprobe.engine import EngineResult, transient_kind
from hookprobe.runs import RunStore
from hookprobe.service import RunService
from tests.helpers import FakeEngine, make_settings


def _service(tmp_path, error: str):
    settings = make_settings(tmp_path, workdir=tmp_path, alarm_url="https://alarm.invalid/hook")
    fake = FakeEngine(result=EngineResult(text="", message_count=1, error=error))
    service = RunService(settings, fake, RunStore(tmp_path / "results"))
    service.retry_backoff_seconds = 0
    sent: list[str] = []

    async def alarm(text: str) -> bool:
        sent.append(text)
        return True

    service.alarm = alarm  # type: ignore[method-assign]
    return service, fake, sent


async def _run(service: RunService, key: str):
    service.start({"message": "Title: t\ngo", "sessionKey": key})
    for _ in range(800):
        run = service.get(key)
        if run and run.finished:
            await asyncio.sleep(0.02)  # let a detached alarm land
            return run
        await asyncio.sleep(0.01)
    raise AssertionError(f"{key} never finished")


def _paged(sent: list[str]) -> list[str]:
    return [text for text in sent if "keep failing" in text]


def test_every_transient_marker_has_a_kind() -> None:
    assert all(transient_kind(f"API Error: {marker}") for marker in engine_module._TRANSIENT_MARKERS)
    assert transient_kind("429 Too Many Requests") == transient_kind("rate limit exceeded") == "rate limited"
    assert transient_kind("context window exceeded") is None


def test_the_third_rate_limited_run_pages_once_and_a_landing_says_so(tmp_path) -> None:
    service, fake, sent = _service(tmp_path, "API Error: 429 rate limit exceeded")

    async def scenario() -> None:
        for i in range(2):
            await _run(service, f"probe:alert:{i}")
        assert _paged(sent) == [], "two in a row is still bad luck"
        await _run(service, "probe:alert:2")
        assert len(_paged(sent)) == 1 and "rate limited" in sent[-1] and "3 in a row" in sent[-1]
        await _run(service, "probe:alert:3")
        assert len(_paged(sent)) == 1, "the same kind pages once"
        fake.result = FakeEngine().result
        await _run(service, "probe:alert:ok")
        assert "landing again after 4 transient failures" in sent[-1]

    asyncio.run(scenario())


def test_a_different_kind_starts_its_own_count(tmp_path) -> None:
    service, fake, sent = _service(tmp_path, "API Error: 429")

    async def scenario() -> None:
        for i in range(2):
            await _run(service, f"probe:alert:a{i}")
        fake.result = EngineResult(text="", message_count=1, error="API Error: 503 Service Unavailable")
        for i in range(2):
            await _run(service, f"probe:alert:b{i}")
        assert _paged(sent) == []
        await _run(service, "probe:alert:b2")
        assert len(_paged(sent)) == 1 and "5xx" in sent[-1]

    asyncio.run(scenario())


def test_one_investigations_failure_and_a_node_wide_one_are_not_counted(tmp_path) -> None:
    for error in ("context window exceeded", "API Error: connection error"):
        service, _, sent = _service(tmp_path, error)

        async def scenario(service=service, tag=error[:5]) -> None:
            for i in range(4):
                await _run(service, f"probe:alert:{tag}{i}")

        asyncio.run(scenario())
        assert _paged(sent) == [], error


def test_the_same_kind_pages_again_after_six_hours(tmp_path, monkeypatch) -> None:
    service, _, sent = _service(tmp_path, "API Error: 529 overloaded")

    async def scenario() -> None:
        for i in range(3):
            await _run(service, f"probe:alert:{i}")
        assert len(_paged(sent)) == 1
        service._streak_paged["overloaded"] -= service_module._RECURRING_QUIET_SECONDS + 1
        await _run(service, "probe:alert:3")
        assert len(_paged(sent)) == 2

    asyncio.run(scenario())
