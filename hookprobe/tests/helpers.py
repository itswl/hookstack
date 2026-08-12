"""Shared test doubles: a Settings factory and engines that never touch the SDK."""

from __future__ import annotations

import asyncio
import threading
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
    ) -> None:
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

    async def run(self, *, message: str, session_key: str, resume: str | None = None) -> EngineResult:
        self.calls += 1
        self.messages.append(message)
        self.resumes.append(resume)
        self.running += 1
        self.max_running = max(self.max_running, self.running)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
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

    async def run(self, *, message: str, session_key: str, resume: str | None = None) -> EngineResult:
        self.resumes.append(resume)
        while not self.release.is_set():
            await asyncio.sleep(0.01)
        return self.result
