"""Shared test doubles: a Settings factory and engines that never touch the SDK."""

from __future__ import annotations

import asyncio
import threading
from dataclasses import replace
from pathlib import Path

from hookprobe.engine import EngineResult
from hookprobe.settings import Settings


def make_settings(tmp_path: Path, **overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "token": "secret-token",
        "model": "claude-opus-5",
        "max_turns": 8,
        "max_concurrent": 2,
        "default_timeout_seconds": 5,
        "max_timeout_seconds": 10,
        "workdir": tmp_path,
        "mcp_config": None,
        "setting_sources": ("project",),
        "skills": "",
        "system_prompt_append": None,
        "agents_config": None,
        "repeat_reminder_at": 3,
        "bash_timeout_ms": 120000,
        "bash_max_timeout_ms": 600000,
        # Off by default here too: a test that wants the loop must say so, so
        # that no other test quietly starts writing runbooks into its tmp dir.
        "auto_distill_max": 0,
        # Off in the fixture for the same reason auto-distill is: a test that
        # wants the pass says so, and nobody else pays a surprise model run.
        "consolidate_at": 0,
        "remediation_allowlist": None,
        "coalesce_window_seconds": 1800,
        "event_secret": "",
        "return_url": "",
        "return_secret": "",
        "escalate_levels": frozenset({"critical", "high"}),
        "budget_usd": 0.0,
        "budget_window_hours": 24.0,
        "retention_days": 0,
        "alarm_url": "",
        "alarm_min_interval_seconds": 600,
        "host": "127.0.0.1",
        "port": 0,
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


class FakeEngine:
    def __init__(
        self,
        result: EngineResult | None = None,
        exc: Exception | None = None,
        delay: float = 0.0,
        events: list[dict] | None = None,
    ) -> None:
        self.events = events or []
        self.result = result or EngineResult(
            text='{"summary": "ok"}',
            message_count=3,
            cost_usd=0.5,
            session_id="sdk-session-1",
            usage={
                "input_tokens": 12,
                "output_tokens": 34,
                "cache_read_input_tokens": 56,
                "cache_creation_input_tokens": 7,
            },
            model_usage={"claude-opus-5": {"inputTokens": 12}},
            duration_ms=1234,
        )
        self.exc = exc
        self.delay = delay
        self.calls = 0
        self.running = 0
        self.max_running = 0
        self.messages: list[str] = []
        self.resumes: list[str | None] = []
        self.described: list[str | None] = []
        # The interrupt protocol. `stoppable` False plays an SDK that ignores the
        # ask, which is what the caller's cancel fallback is for.
        self.stoppable = True
        self.interrupts = 0
        self.interrupted_cost = 0.25
        self._interrupted = asyncio.Event()

    def describe_inputs(self, *, resume: str | None = None) -> dict:
        self.described.append(resume)
        return {"model": "claude-opus-5", "resumed": bool(resume)}

    async def stop(self) -> bool:
        """Model the SDK's interrupt: the turn winds down and still reports.

        A fake that only returned True would prove nothing — the whole point of
        the interrupt is that the turn ENDS AND STILL BILLS, so this has to
        actually end it. `interrupts` counts the asks, and `stoppable` lets a
        test play the case where the SDK ignores one, which is the branch the
        cancel fallback exists for.
        """
        self.interrupts += 1
        if not self.stoppable:
            return False
        self._interrupted.set()
        return True

    async def run(self, *, message: str, session_key: str, resume: str | None = None, on_event=None) -> EngineResult:
        self.calls += 1
        self.messages.append(message)
        self.resumes.append(resume)
        self.running += 1
        self.max_running = max(self.max_running, self.running)
        self._interrupted = asyncio.Event()
        try:
            if self.delay:
                # Interruptible sleep: a real turn stops early when asked, and a
                # fake that slept through it would make the interrupt untestable.
                try:
                    await asyncio.wait_for(self._interrupted.wait(), timeout=self.delay)
                except TimeoutError:
                    pass
                else:
                    # Asked to stop: report the bill for what was spent, which is
                    # exactly what cancelling used to throw away.
                    return replace(self.result, text="(interrupted)", cost_usd=self.interrupted_cost)
            if on_event is not None:
                for event in self.events:
                    on_event(dict(event))
            if self.exc is not None:
                raise self.exc
            return self.result
        finally:
            self.running -= 1


class GatedEngine:
    """Blocks until released from the test thread (thread-safe by polling)."""

    def __init__(self, result: EngineResult | None = None) -> None:
        self.result = result or EngineResult(text='{"summary": "done"}', message_count=2, session_id="sdk-session-g")
        self.release = threading.Event()
        self.resumes: list[str | None] = []

    def describe_inputs(self, *, resume: str | None = None) -> dict:
        return {"model": "claude-opus-5", "resumed": bool(resume)}

    async def run(self, *, message: str, session_key: str, resume: str | None = None, on_event=None) -> EngineResult:
        self.resumes.append(resume)
        while not self.release.is_set():
            await asyncio.sleep(0.01)
        return self.result
