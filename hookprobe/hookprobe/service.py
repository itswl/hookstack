"""Orchestration: accept a trigger, run the engine once, remember the outcome.

The failure shape is a deliberate choice: a run that dies (exception,
timeout, empty output) still completes the WebhookWise contract — isFinal
true, with a well-formed report whose root_cause says the runner failed.
The operator sees the error on the analysis card within one poll instead of
waiting out WebhookWise's full timeout window.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Callable
from typing import Any, Protocol

from hookprobe.engine import EngineResult
from hookprobe.runs import COMPLETED, FAILED, RUNNING, Run, RunStore
from hookprobe.settings import Settings

logger = logging.getLogger("hookprobe.service")


class Engine(Protocol):
    async def run(
        self,
        *,
        message: str,
        session_key: str,
        resume: str | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> EngineResult: ...


class RunBusyError(RuntimeError):
    """The session already has a turn in flight."""


class NotResumableError(ValueError):
    """The run left no engine session behind to resume."""


def failure_report(reason: str) -> str:
    """A minimal deep_analysis_report-shaped JSON so WebhookWise renders the failure."""
    return json.dumps(
        {
            "summary": f"hookprobe run failed: {reason}",
            "root_cause": {
                "status": "unknown",
                "description": f"The analysis runner failed before reaching a conclusion: {reason}",
            },
            "evidence": [],
            "impact": {
                "scope": "analysis pipeline",
                "severity": "unknown",
                "description": "No analysis was produced for this alert.",
            },
            "timeline": [],
            "recommendations": [
                {
                    "priority": "P1",
                    "action": "Retry the deep analysis from the WebhookWise dashboard",
                    "reason": "The failure was in the runner, not necessarily in the alert itself.",
                }
            ],
            "unknowns": ["The investigation did not run to completion."],
            "assumptions": [],
            "next_checks": [],
            "confidence": 0.0,
        },
        ensure_ascii=False,
    )


class RunService:
    def __init__(self, settings: Settings, engine: Engine, store: RunStore) -> None:
        self._settings = settings
        self._engine = engine
        self._store = store
        self._semaphore = asyncio.Semaphore(settings.max_concurrent)
        self._tasks: set[asyncio.Task[None]] = set()

    def start(self, payload: dict[str, Any]) -> Run:
        """Idempotent per sessionKey: re-triggering an existing run returns it."""
        session_key = str(payload.get("sessionKey") or "") or f"hookprobe:{uuid.uuid4()}"
        existing = self._store.get(session_key)
        if existing is not None:
            return existing

        message = str(payload.get("message") or "")
        if not message.strip():
            raise ValueError("message must not be empty")

        timeout_s = self._clamp_timeout(payload.get("timeoutSeconds"))
        run = Run(
            session_key=session_key,
            run_id=uuid.uuid4().hex[:12],
            current_message=message,
            model=self._settings.model,
        )
        self._store.create(run)
        self._spawn(run, message, timeout_s, resume=None)
        return run

    def continue_run(self, session_key: str, payload: dict[str, Any]) -> Run:
        """Reopen a finished investigation with a follow-up message.

        The engine session keeps everything the first pass gathered — tool
        output, evidence, dead ends — so the follow-up explores from there
        instead of starting cold. /final then serves the newest answer.
        """
        run = self._store.get(session_key)
        if run is None:
            raise LookupError("session not found")
        if not run.finished:
            raise RunBusyError("a turn is already in progress for this session")
        if not run.engine_session_id:
            raise NotResumableError("this run left no engine session to resume")

        message = str(payload.get("message") or "")
        if not message.strip():
            raise ValueError("message must not be empty")

        timeout_s = self._clamp_timeout(payload.get("timeoutSeconds"))
        run.status = RUNNING
        run.model = run.model or self._settings.model  # backfill for pre-model records
        run.run_id = uuid.uuid4().hex[:12]
        run.text = ""
        run.error = None
        run.finished_at = None
        run.current_message = message
        self._spawn(run, message, timeout_s, resume=run.engine_session_id)
        return run

    def _spawn(self, run: Run, message: str, timeout_s: int, *, resume: str | None) -> None:
        run.events = []
        task = asyncio.create_task(self._execute(run, message, timeout_s, resume=resume))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def get(self, session_key: str) -> Run | None:
        return self._store.get(session_key)

    def list_runs(self, limit: int = 100) -> list[Run]:
        return self._store.list_runs(limit=limit)

    def active_count(self) -> int:
        return self._store.active_count()

    def _clamp_timeout(self, raw: Any) -> int:
        try:
            timeout_s = int(raw)
        except (TypeError, ValueError):
            timeout_s = self._settings.default_timeout_seconds
        if timeout_s <= 0:
            timeout_s = self._settings.default_timeout_seconds
        return min(timeout_s, self._settings.max_timeout_seconds)

    async def _execute(self, run: Run, message: str, timeout_s: int, *, resume: str | None = None) -> None:
        logger.info("run start session=%s timeout=%ss resume=%s", run.session_key, timeout_s, resume or "-")

        def on_event(event: dict[str, Any]) -> None:
            event["ts"] = time.time()
            run.events.append(event)
            if len(run.events) > 400:  # bound memory and the result file
                del run.events[: len(run.events) - 400]

        try:
            # The semaphore sits outside the timeout: a queued run's clock
            # starts when it gets a slot, not while it waits for one.
            async with self._semaphore:
                result = await asyncio.wait_for(
                    self._engine.run(message=message, session_key=run.session_key, resume=resume, on_event=on_event),
                    timeout=timeout_s,
                )
        except TimeoutError:
            self._fail(run, f"timed out after {timeout_s}s")
            return
        except asyncio.CancelledError:
            self._fail(run, "cancelled during shutdown")
            raise
        except Exception as exc:  # noqa: BLE001 — the run must always reach a final state
            logger.exception("run crashed session=%s", run.session_key)
            self._fail(run, f"{type(exc).__name__}: {exc}")
            return

        run.message_count = result.message_count
        run.cost_usd = result.cost_usd
        if result.session_id:
            run.engine_session_id = result.session_id
        if result.error:
            self._fail(run, result.error, result)
            return

        run.status = COMPLETED
        run.text = result.text
        self._record_turn(run, result)
        self._store.finish(run)
        logger.info(
            "run completed session=%s turns=%s cost_usd=%s",
            run.session_key,
            result.message_count,
            result.cost_usd,
        )

    def _fail(self, run: Run, reason: str, result: EngineResult | None = None) -> None:
        run.status = FAILED
        run.error = reason
        run.text = failure_report(reason)
        self._record_turn(run, result)
        self._store.finish(run)
        logger.warning("run failed session=%s reason=%s", run.session_key, reason)

    def _record_turn(self, run: Run, result: EngineResult | None) -> None:
        run.turns.append(
            {
                "message": run.current_message,
                "text": run.text,
                "error": run.error,
                "run_id": run.run_id,
                "cost_usd": run.cost_usd,
                "finished_at": time.time(),
                "usage": result.usage if result else None,
                "model_usage": result.model_usage if result else None,
                "duration_ms": result.duration_ms if result else None,
                "events": list(run.events),
            }
        )
