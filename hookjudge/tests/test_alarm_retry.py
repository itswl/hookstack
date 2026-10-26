"""The judge's alarm tries a connection that did not happen again, in place.

An alarm is one message with nothing behind it that resends it. Any answer from
the channel, whatever its status, is not retried — httpx does not raise for one.
"""

from __future__ import annotations

import httpx
import pytest

from hookjudge import alarm as alarm_module
from hookjudge.alarm import SelfAlarm


@pytest.fixture(autouse=True)
def _no_pause(monkeypatch):
    monkeypatch.setattr(alarm_module, "RETRY_DELAY_SECONDS", 0)


class _Client:
    def __init__(self, misses: int) -> None:
        self.misses = misses
        self.posts: list[str] = []

    async def post(self, url: str, *, json: dict, timeout: float) -> object:
        self.posts.append(json["content"]["text"])
        if len(self.posts) <= self.misses:
            raise httpx.ConnectError("[Errno -2] Name or service not known")
        return object()


async def test_a_dead_return_alarm_survives_a_moment_without_a_resolver() -> None:
    alarm = SelfAlarm("https://bot.example/hook", min_interval_seconds=600)
    client = _Client(misses=2)
    await alarm.dead_return(client, title="disk full on db-1", error="pipe answered 503", now=1000.0)
    assert len(client.posts) == 3 and "disk full on db-1" in client.posts[-1]


async def test_after_its_retries_it_gives_up_owes_the_count_and_the_next_one_tries() -> None:
    alarm = SelfAlarm("https://bot.example/hook", min_interval_seconds=600)
    down = _Client(misses=99)
    await alarm.dead_return(down, title="a", error="e", now=1000.0)  # must not raise
    assert len(down.posts) == alarm_module.RETRIES + 1
    up = _Client(misses=0)
    await alarm.dead_return(up, title="b", error="e", now=1001.0)
    assert len(up.posts) == 1 and "1 more folded" in up.posts[0], "the lost message is owed to the next"
