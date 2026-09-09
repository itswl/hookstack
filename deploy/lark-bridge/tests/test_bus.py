"""The preventive recycle: what it runs, and when it declines to.

The mechanism itself was proven live rather than here — stopping the bus on a
running deployment and watching both consumers come back is not something a
unit test can assert, and the numbers are in `recycle_bus`'s docstring. What
these pin is the part a later edit can silently break: the argv, the shared
clock, and the off switch.
"""

from __future__ import annotations

import subprocess
import time
from typing import Any

import bridge


def test_the_recycle_forces_past_its_own_consumers(monkeypatch: Any) -> None:
    """Without --force the CLI refuses with exit 2 while consumers are attached
    — which is always, since this bridge's two are the ones attached. A recycle
    that quietly does nothing is worse than none: the silence it was called for
    continues, and now something in the log claims it was handled."""
    seen: list[list[str]] = []

    def fake(args: list[str], stdin: str | None = None, timeout: int = 60) -> Any:
        seen.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="{}", stderr="")

    monkeypatch.setattr(bridge, "_lark", fake)
    bridge.recycle_bus()
    assert seen == [["event", "stop", "--force", "--json"]]


def test_a_refused_recycle_is_logged_and_survived(monkeypatch: Any) -> None:
    """The watchdog thread is the only thing keeping the clock; an exception
    here would end it and the bridge would lose the feature silently."""

    def refuses(args: list[str], stdin: str | None = None, timeout: int = 60) -> Any:
        return subprocess.CompletedProcess(args, 2, stdout="", stderr="still has active consumers")

    monkeypatch.setattr(bridge, "_lark", refuses)
    bridge.recycle_bus()  # must not raise

    def explodes(args: list[str], stdin: str | None = None, timeout: int = 60) -> Any:
        raise OSError("no such binary")

    monkeypatch.setattr(bridge, "_lark", explodes)
    bridge.recycle_bus()  # must not raise


def test_any_stream_resets_the_clock() -> None:
    """One connection, two consumers: a message proves the press stream's socket
    exactly as much as a press does. Per-consumer idleness would have recycled a
    healthy bus nightly — production's press stream saw zero presses in 28h
    while the message stream took twelve."""
    assert bridge.idle_seconds() >= 0.0
    bridge._last_event_at = time.monotonic() - 10_000
    assert bridge.idle_seconds() > 9_000
    bridge.note_event()
    assert bridge.idle_seconds() < 1.0


def test_zero_turns_it_off(monkeypatch: Any) -> None:
    """A deployment that would rather keep a possibly-dead connection than pay a
    reconnect it did not need says so with 0, and nothing runs."""

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the CLI was reached with recycling off")

    monkeypatch.setattr(bridge, "_lark", fail)
    monkeypatch.setattr(bridge, "IDLE_RECYCLE_SECONDS", 0)
    bridge.recycle_when_idle()  # returns rather than looping
