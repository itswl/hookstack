"""Telling an open board that something changed, instead of it asking.

A board is watched while something is happening, and what it shows changes when
this service writes — not when a clock in the browser says so. Polling made the
operator choose between a stale board and a busy one; this removes the choice by
letting the write itself be the signal.

Deliberately thin: the notification carries no payload. A board has filters, a
window and a limit of its own, so "something changed, look again" is both smaller
and more correct than shipping rows the viewer may not be asking for.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator


class Live:
    """One process's watchers, and the writes that wake them."""

    def __init__(self) -> None:
        self._watchers: set[asyncio.Queue[str]] = set()

    def watch(self) -> asyncio.Queue[str]:
        # Depth 1 is the whole design: consecutive writes collapse into one
        # "look again", because that is exactly what a watcher would do anyway.
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=1)
        self._watchers.add(queue)
        return queue

    def unwatch(self, queue: asyncio.Queue[str]) -> None:
        self._watchers.discard(queue)

    def changed(self) -> None:
        """Wake every watcher. Never raises: a broken board must not break a write."""
        for queue in tuple(self._watchers):
            if queue.empty():
                # A racing reader can fill it between the check and the put;
                # losing that one is harmless, the queued wake-up says the same.
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait("changed")

    @property
    def watcher_count(self) -> int:
        return len(self._watchers)

    async def stream(self, *, keepalive_seconds: float = 20.0, settle_seconds: float = 0.3) -> AsyncIterator[bytes]:
        """NDJSON: a `changed` per burst of writes, a `ping` through the quiet.

        Per *burst*, not per write, and the difference is the whole point. One
        alert touches the ledger many times — the event, the decision, a queued
        delivery per channel, each send — and a board that refetched on every
        one of them would work harder under load than the poll loop this
        replaced. So a wake-up opens a short settling window, whatever else
        arrives inside it is absorbed, and the board looks once.

        The ping is not decoration: proxies close silent connections, and a quiet
        service is the normal case here.
        """
        queue = self.watch()
        try:
            yield _line({"type": "hello"})
            while True:
                try:
                    await asyncio.wait_for(queue.get(), timeout=keepalive_seconds)
                except TimeoutError:
                    yield _line({"type": "ping"})
                    continue
                if settle_seconds > 0:
                    await asyncio.sleep(settle_seconds)
                    while not queue.empty():
                        queue.get_nowait()
                yield _line({"type": "changed"})
        finally:
            self.unwatch(queue)


def _line(payload: dict[str, object]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
