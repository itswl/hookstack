"""Shared test doubles: a Settings factory and engines that never touch the SDK."""

from __future__ import annotations

import asyncio
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any

from hookprobe import remediation
from hookprobe.engine import EngineResult
from hookprobe.settings import Settings


def make_settings(tmp_path: Path, **overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "token": "secret-token",
        # A DIFFERENT value on purpose: a test that passes with the two equal
        # would prove nothing about the split.
        "agent_token": "agent-token",
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
        "budget_gates_agent_door": False,
        "ruling_ttl_days": 14,
        "ruling_reverify_days": 7,
        "runbook_answer_days": 0,
        "refire_answer_hours": 0,
        "decline_patterns": None,
        "synthetic_key_prefixes": frozenset({"manual:", "drill:"}),
        "bash_timeout_ms": 120000,
        "bash_max_timeout_ms": 600000,
        # Off by default here too: a test that wants the loop must say so, so
        # that no other test quietly starts writing runbooks into its tmp dir.
        "auto_distill_max": 0,
        # Off in the fixture for the same reason auto-distill is: a test that
        # wants the pass says so, and nobody else pays a surprise model run.
        "consolidate_at": 0,
        "remediation_allowlist": None,
        "blast_radius": None,
        "remediation_high_risk_allowlist": None,
        "remediation_cooldown_seconds": remediation.COOLDOWN_SECONDS,
        "coalesce_window_seconds": 1800,
        "event_secret": "",
        "return_url": "",
        "relay_ui_url": "",
        # Empty like every deployment: a node here has no reachable address.
        "public_url": "",
        # Off, like every deployment: `cost_usd` stays whatever the runtime
        # reported unless a test states rates.
        "price_in_per_1m": 0.0,
        "price_cache_read_per_1m": 0.0,
        "price_cache_write_per_1m": 0.0,
        "price_out_per_1m": 0.0,
        "resume_interrupted": True,
        "agent_name": "hookprobe",
        "agent_role": "",
        # Off by default: a test that wants rulings filed sets both, and every
        # other one gets the markers stripped with nothing posted — which is also
        # the production default until an operator wires the door.
        "ruling_url": "",
        "ruling_secret": "",
        # OFF in tests, ON in production. Most tests here assert the queue's
        # behaviour, and a default that quietly applied half their fixtures to
        # CLAUDE.md would make them pass for the wrong reason.
        "memory_auto_apply": False,
        "automation_tiers": {},
        "return_secret": "",
        "escalate_levels": frozenset({"critical", "high"}),
        # Closed, like production: a test that wants an MCP tool names it.
        "mcp_tools": frozenset(),
        # Empty, like production: a test that wants a report to conclude with a
        # routable label declares the vocabulary, so no other test acquires a
        # new routing input by existing.
        "verdicts": frozenset(),
        "budget_usd": 0.0,
        "budget_window_hours": 24.0,
        "handoff_url": "",
        "handoff_secret": "",
        "bash_guard": "readonly",
        "posture_check": "off",
        "telemetry_receiver": "on",
        "follow_up_senders": frozenset(),
        "retention_days": 0,
        "alarm_url": "",
        "alarm_min_interval_seconds": 600,
        "selftest_every_seconds": 0,
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
        # One tool call by default, because an investigation that looked at
        # nothing is not an investigation and no longer distils a runbook
        # (distill.auto_write). A fake that emits no steps was standing in for
        # something that cannot happen in production: every real run on the
        # deployment used at least one tool. Pass `events=[]` explicitly for the
        # tests that DO want a run which looked at nothing.
        self.events = [{"type": "tool_use", "name": "Bash", "detail": "df -h"}] if events is None else events
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
        return {
            "model": "claude-opus-5",
            "resumed": bool(resume),
            "posture": {"bash_guard": "readonly", "mcp_tools": []},
        }

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
        return {
            "model": "claude-opus-5",
            "resumed": bool(resume),
            "posture": {"bash_guard": "readonly", "mcp_tools": []},
        }

    async def run(self, *, message: str, session_key: str, resume: str | None = None, on_event=None) -> EngineResult:
        self.resumes.append(resume)
        while not self.release.is_set():
            await asyncio.sleep(0.01)
        return self.result


class UnscriptedCall(AssertionError):
    """The loop talked to the runtime in a way the test did not script."""


class ScriptedTurns:
    """A ClaudeSDKClient stand-in that scripts turns AND polices the traffic.

    Borrowed in shape from `agents.testing.ScriptedModel` in
    openai/openai-agents-python, which does one thing this repository's fakes
    did not: it fails when the loop makes a model call the test never scripted,
    and when scripted steps go unconsumed.

    `FakeEngine` is injected at the `Engine` protocol boundary, so it never
    reaches the receive loop at all — and inside that loop are the cost
    accounting, the interrupt path, the redaction capture point and the input
    fingerprint diff. `test_engine_loop.py` records what that gap cost: two
    bugs in one day, both one assertion away from being caught. Its own fake
    closed half of it by scripting ONE stream; this closes the other half by
    counting the calls, which on a product whose central argument is cost is
    the property worth pinning. A loop that quietly asks the runtime twice is
    a turn billed twice.

    Usage:

        client = ScriptedTurns([[assistant("ok"), result()]])
        monkeypatch.setattr(claude_agent_sdk, "ClaudeSDKClient", client.factory())
        ...
        client.assert_consumed()
    """

    def __init__(self, turns: list[list[Any]], *, context_usage: Any = None) -> None:
        self._turns = list(turns)
        self._context_usage = context_usage
        self.queries: list[str] = []
        self.calls: list[str] = []
        self.connects = 0
        self.disconnects = 0
        self.interrupts = 0
        self.context_usage_asks = 0

    def factory(self) -> Any:
        """What `ClaudeSDKClient` is replaced with: every construction is this."""
        outer = self

        class _Client:
            def __init__(self, options: Any = None) -> None:
                self.options = options
                outer.options = options

            async def connect(self) -> None:
                outer.calls.append("connect")
                outer.connects += 1

            async def disconnect(self) -> None:
                outer.calls.append("disconnect")
                outer.disconnects += 1

            async def query(self, message: str) -> None:
                outer.calls.append("query")
                if not outer._turns:
                    raise UnscriptedCall(
                        f"the loop asked the runtime for turn {len(outer.queries) + 1} and the test "
                        f"scripted {len(outer.queries)}. An unscripted turn is a turn nobody budgeted."
                    )
                outer.queries.append(message)

            async def interrupt(self) -> None:
                outer.calls.append("interrupt")
                outer.interrupts += 1

            async def get_context_usage(self) -> Any:
                outer.calls.append("get_context_usage")
                outer.context_usage_asks += 1
                if isinstance(outer._context_usage, Exception):
                    raise outer._context_usage
                if outer._context_usage is None:
                    raise RuntimeError("this runtime does not report context usage")
                return outer._context_usage

            async def receive_response(self):
                for message in outer._turns.pop(0):
                    yield message

        return _Client

    def assert_consumed(self) -> None:
        """Every scripted turn was used. A loop that stops early is a bug the
        old fake could not see: it replayed one stream and said nothing about
        the turns the test expected and never got."""
        if self._turns:
            raise UnscriptedCall(
                f"{len(self._turns)} scripted turn(s) unconsumed — the loop stopped before the test expected"
            )
