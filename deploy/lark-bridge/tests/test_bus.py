"""The bus this process does not own: recycling it, and asking what it delivered.

The mechanism itself was proven live rather than here — stopping the bus on a
running deployment and watching both consumers come back is not something a
unit test can assert, and the numbers are in `recycle_bus`'s docstring. What
these pin is the part a later edit can silently break: the argv, the shared
clock, and the off switch.
"""

from __future__ import annotations

import json
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


# ── shape 4: has anybody opened these? ────────────────────────────────────────


def test_read_status_counts_and_never_names(monkeypatch: Any) -> None:
    """Counts and timestamps, never who. The identities are the part a pipe's
    ledger has no business holding — this bridge carries messages, it does not
    build a record of who reads them."""
    answer = {
        "ok": True,
        "data": {"items": [{"timestamp": "1788963718000", "user_id": "ou_a"}, {"timestamp": "1788963999000"}]},
    }

    def fake(args: list[str], stdin: str | None = None, timeout: int = 60) -> Any:
        assert args[1] == "GET" and args[2].endswith("/read_users")
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(answer), stderr="")

    monkeypatch.setattr(bridge, "_lark", fake)
    out = bridge.read_status(["om_1"])
    assert out == {"om_1": {"readers": 2, "first_read_at": 1788963718.0}}
    assert "ou_a" not in json.dumps(out)


def test_nobody_read_it_and_cannot_tell_are_different_answers(monkeypatch: Any) -> None:
    """The whole value of this is telling those two apart, so a message the
    platform will not report on must not come back looking like an unread one."""

    def unread(args: list[str], stdin: str | None = None, timeout: int = 60) -> Any:
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps({"ok": True, "data": {"items": []}}), stderr="")

    monkeypatch.setattr(bridge, "_lark", unread)
    assert bridge.read_status(["om_1"]) == {"om_1": {"readers": 0, "first_read_at": None}}

    def refused(args: list[str], stdin: str | None = None, timeout: int = 60) -> Any:
        body = {"ok": False, "error": {"message": "message not found"}}
        return subprocess.CompletedProcess(args, 1, stdout=json.dumps(body), stderr="")

    monkeypatch.setattr(bridge, "_lark", refused)
    assert bridge.read_status(["om_1"]) == {"om_1": {"error": "message not found"}}

    def explodes(args: list[str], stdin: str | None = None, timeout: int = 60) -> Any:
        raise OSError("no such binary")

    monkeypatch.setattr(bridge, "_lark", explodes)
    assert "error" in bridge.read_status(["om_1"])["om_1"]


def test_the_batch_is_bounded_because_each_id_is_a_call(monkeypatch: Any) -> None:
    calls: list[str] = []

    def fake(args: list[str], stdin: str | None = None, timeout: int = 60) -> Any:
        calls.append(args[2])
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps({"ok": True, "data": {"items": []}}), stderr="")

    monkeypatch.setattr(bridge, "_lark", fake)
    bridge.read_status([f"om_{i}" for i in range(100)])
    assert len(calls) == bridge.READ_BATCH_MAX
