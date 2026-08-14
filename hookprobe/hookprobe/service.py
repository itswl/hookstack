"""Orchestration: accept a trigger, run the engine once, remember the outcome.

The failure shape is a deliberate choice: a run that dies (exception,
timeout, empty output) still completes the contract — isFinal true, with a
well-formed report whose root_cause says the runner failed. The caller sees
the error within one poll instead of waiting out its own timeout window.
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
from hookprobe.wire import sign_timestamped

logger = logging.getLogger("hookprobe.service")


def _report_summary(text: str) -> str:
    """The one paragraph a channel card shows; the full text stays on the run."""
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict) and parsed.get("summary"):
            return str(parsed["summary"])[:800]
    except (TypeError, ValueError):
        pass
    return text.strip()[:800]


class Engine(Protocol):
    async def run(
        self,
        *,
        message: str,
        session_key: str,
        resume: str | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> EngineResult: ...

    def describe_inputs(self, *, resume: str | None = None) -> dict[str, Any]:
        """The prompt inputs this engine would resolve for the next turn."""
        ...


class RunBusyError(RuntimeError):
    """The session already has a turn in flight."""


class NoTurnRunningError(RuntimeError):
    """Stop was asked for, but nothing is in flight."""


class NotResumableError(ValueError):
    """The run left no engine session behind to resume."""


def failure_report(reason: str) -> str:
    """A minimal report-shaped JSON so an OpenClaw-dialect caller renders the failure."""
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
                    "action": "Retry the analysis from the caller's side",
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


def budget_report(spent: float, budget: float, window_hours: float) -> str:
    """A report-shaped refusal, so the family loop completes without an engine run."""
    summary = (
        f"Budget breaker open: investigations have spent ${spent:.2f} in the last "
        f"{window_hours:g}h (budget ${budget:.2f}), so this alert was NOT investigated. "
        "The judge's verdict is unaffected. Investigations resume when the window slides "
        "or HOOKPROBE_BUDGET_USD is raised."
    )
    return json.dumps(
        {
            "summary": summary,
            "root_cause": {
                "status": "not_investigated",
                "description": "The investigation budget for the current window is exhausted; "
                "the run was refused before the engine started.",
            },
            "evidence": [],
            "impact": {
                "scope": "analysis pipeline",
                "severity": "none",
                "description": "Only the deep investigation was skipped; the alert and its verdict are unaffected.",
            },
            "timeline": [],
            "recommendations": [
                {
                    "priority": "P2",
                    "action": "Raise HOOKPROBE_BUDGET_USD or wait for the window to slide, "
                    "then re-send the event if the alert still matters",
                    "reason": "The breaker refuses new autonomous investigations; it does not queue them.",
                }
            ],
            "unknowns": ["No investigation was run for this alert."],
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
        self._running: dict[str, asyncio.Task[None]] = {}
        self._stop_requested: set[str] = set()
        self._in_slot = 0
        # Live watchers of a session's process feed, one queue each. A run
        # already publishes its steps through on_event; this is the seam that
        # lets a browser see them as they happen instead of on the next poll.
        self._watchers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = {}
        # Set by the app: "the session list moved" — a run started, settled, or
        # returned its report. Separate from the per-run feed above, because a
        # list and a transcript answer different questions.
        self.on_board_change: Callable[[], None] | None = None
        # Return-retry pacing, an instance attr so tests can collapse it.
        self._return_delays: tuple[float, ...] = (0.0, 2.0, 5.0)
        # Self-alarm throttle state (see _alarm_return_failure).
        self._alarm_last_sent = 0.0
        self._alarm_suppressed = 0

    def start(self, payload: dict[str, Any], *, origin: str = "") -> Run:
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
            origin=origin,
        )
        run.meta = dict(payload.get("_meta") or {})
        self._store.create(run)
        self._board_changed()
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

    def stop(self, session_key: str) -> Run:
        """Cancel the in-flight turn; it finishes as a failed turn, not a hang."""
        run = self._store.get(session_key)
        if run is None:
            raise LookupError("session not found")
        task = self._running.get(session_key)
        if run.finished or task is None:
            raise NoTurnRunningError("no turn is in flight for this session")
        self._stop_requested.add(session_key)
        task.cancel()
        return run

    def _spawn(self, run: Run, message: str, timeout_s: int, *, resume: str | None) -> None:
        run.events = []
        self._stop_requested.discard(run.session_key)
        # Checkpoint before the task exists: if the process dies mid-flight,
        # the next boot's sweep finds this stub and completes the loop.
        self._store.checkpoint(run)
        task = asyncio.create_task(self._execute(run, message, timeout_s, resume=resume))
        self._tasks.add(task)
        self._running[run.session_key] = task

        def _done(t: asyncio.Task[None], key: str = run.session_key) -> None:
            self._tasks.discard(t)
            if self._running.get(key) is t:
                del self._running[key]

        task.add_done_callback(_done)

    def sweep_orphans(self) -> int:
        """Settle runs a previous process left mid-flight, at startup.

        Live state does not survive a restart, but a relay-born investigation
        has no poller on the other side — only a pipe waiting for probe-notify.
        Silence would break "failure completes the loop", so every orphan
        becomes a failed run that reports itself like any other failure.
        """
        swept = 0
        for run in self._store.list_runs(limit=1000):
            if run.finished or run.session_key in self._running:
                continue
            self._fail(run, "interrupted by a restart before the investigation finished")
            swept += 1
        if swept:
            logger.warning("swept %s orphaned run(s) left by a previous process", swept)
        return swept

    def budget_state(self) -> tuple[float, float] | None:
        """(spent_in_window, budget) — None when the breaker is disabled."""
        if self._settings.budget_usd <= 0:
            return None
        return self.window_spend(), self._settings.budget_usd

    def window_spend(self) -> float:
        """What the window has cost so far, ceiling or no ceiling.

        Knowing the spend and capping it are different questions: an operator
        wants the first answered even when they have chosen not to ask the
        second.
        """
        cutoff = time.time() - self._settings.budget_window_hours * 3600
        return self._store.spend_since(cutoff)

    def refuse_for_budget(self, payload: dict[str, Any], *, origin: str, spent: float) -> Run:
        """Settle the session as a refused run — no engine, cost 0, loop completed.

        Idempotent like start(): if the session already exists (an earlier,
        funded investigation), that run is returned untouched.
        """
        session_key = str(payload.get("sessionKey") or "") or f"hookprobe:{uuid.uuid4()}"
        existing = self._store.get(session_key)
        if existing is not None:
            return existing
        run = Run(
            session_key=session_key,
            run_id=uuid.uuid4().hex[:12],
            current_message=str(payload.get("message") or ""),
            model=self._settings.model,
            origin=origin,
        )
        run.meta = dict(payload.get("_meta") or {})
        self._store.create(run)
        run.status = FAILED
        run.error = (
            f"refused: budget exhausted (${spent:.2f} of ${self._settings.budget_usd:.2f} "
            f"in the last {self._settings.budget_window_hours:g}h)"
        )
        run.cost_usd = 0.0
        run.text = budget_report(spent, self._settings.budget_usd, self._settings.budget_window_hours)
        self._record_turn(run, None)
        self._store.finish(run)
        self._schedule_return(run)
        logger.warning("run refused session=%s reason=%s", run.session_key, run.error)
        return run

    def watch(self, session_key: str) -> asyncio.Queue[dict[str, Any]]:
        """Register a live watcher of one session's feed.

        Bounded on purpose: a browser that stops reading must not let a running
        investigation grow an unbounded backlog in memory. On overflow the
        oldest step is dropped and the watcher is told, which is honest — the
        full account is on the run record either way.
        """
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=200)
        self._watchers.setdefault(session_key, set()).add(queue)
        return queue

    def unwatch(self, session_key: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        watchers = self._watchers.get(session_key)
        if not watchers:
            return
        watchers.discard(queue)
        if not watchers:
            self._watchers.pop(session_key, None)

    def _board_changed(self) -> None:
        if self.on_board_change is not None:
            self.on_board_change()

    def _settle(self, run: Run) -> None:
        """Persist the finished run and wake anyone watching it.

        Without this a watcher would sit on its keepalive until the next timeout
        before noticing the run had ended — the wrong end of the interaction to
        be slow at."""
        self._store.finish(run)
        self._publish(run.session_key, {"type": "settled", "status": run.status, "ts": time.time()})
        self._board_changed()

    def _publish(self, session_key: str, event: dict[str, Any]) -> None:
        """Fan one step out to whoever is watching. Never raises: a broken
        watcher is not a reason to disturb the investigation."""
        for queue in tuple(self._watchers.get(session_key, ())):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                    queue.put_nowait({"type": "dropped", "ts": time.time()})
                except (asyncio.QueueEmpty, asyncio.QueueFull):  # pragma: no cover - racing reader
                    pass

    def get(self, session_key: str) -> Run | None:
        return self._store.get(session_key)

    def list_runs(self, limit: int = 100) -> list[Run]:
        return self._store.list_runs(limit=limit)

    def active_count(self) -> int:
        return self._store.active_count()

    def turn_counts(self) -> tuple[int, int]:
        """(turns holding a slot, turns waiting for one)."""
        return self._in_slot, max(0, len(self._running) - self._in_slot)

    def return_failure_count(self) -> int:
        """Runs whose report never reached the pipe — the number an external
        monitor should alert on if the self-alarm URL is not configured."""
        return sum(1 for run in self._store.list_runs(limit=1000) if run.return_status.startswith("failed"))

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
        # Record what the model is about to see, before it sees it: a failed run
        # is exactly when the loaded memory and skills are worth knowing.
        try:
            run.inputs = self._engine.describe_inputs(resume=resume)
        except Exception:  # noqa: BLE001 — a record of the inputs is not worth a failed run
            logger.debug("describe_inputs failed", exc_info=True)
            run.inputs = {}

        def on_event(event: dict[str, Any]) -> None:
            event["ts"] = time.time()
            # Deltas are for whoever is watching right now: thousands of them per
            # run, and the finished block that follows says the same thing once.
            # Recording them would bury the process feed and bloat every case file.
            if event.get("type") != "delta":
                run.events.append(event)
                if len(run.events) > 400:  # bound memory and the result file
                    del run.events[: len(run.events) - 400]
            self._publish(run.session_key, event)

        try:
            # The semaphore sits outside the timeout: a queued run's clock
            # starts when it gets a slot, not while it waits for one.
            async with self._semaphore:
                self._in_slot += 1
                try:
                    result = await asyncio.wait_for(
                        self._engine.run(
                            message=message, session_key=run.session_key, resume=resume, on_event=on_event
                        ),
                        timeout=timeout_s,
                    )
                finally:
                    self._in_slot -= 1
        except TimeoutError:
            self._fail(run, f"timed out after {timeout_s}s")
            return
        except asyncio.CancelledError:
            if run.session_key in self._stop_requested:
                # Operator hit Stop: cancellation IS the intended outcome, so
                # swallow it and let the run settle as an ordinary failure.
                self._stop_requested.discard(run.session_key)
                self._fail(run, "stopped by operator")
                return
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
        self._settle(run)
        self._schedule_return(run)
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
        self._settle(run)
        self._schedule_return(run)
        logger.warning("run failed session=%s reason=%s", run.session_key, reason)

    # -- the family loop: relay-born runs report back to the pipe ------------

    def _schedule_return(self, run: Run) -> None:
        if run.origin != "relay" or not self._settings.return_url:
            return
        task = asyncio.create_task(self._deliver_return(run))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _deliver_return(self, run: Run) -> None:
        summary = _report_summary(run.text)
        alert_title = str(run.meta.get("title") or run.session_key)
        body = json.dumps(
            {
                # The PROCESSED-EVENT dialect (hookrelay/processed.py): the
                # investigator is a brain, and a brain hands the pipe its
                # RESULT in the one shape every channel renderer knows how to
                # dress. Speaking anything else renders as an empty card.
                "meta": {
                    "alert_name": f"{alert_title} · investigation",
                    "source": str(run.meta.get("source") or "hookprobe"),
                    "importance": str(run.meta.get("level") or "medium"),
                    "event_id": run.meta.get("event_id"),
                    "brain": "hookprobe",
                    "timestamp": time.time(),
                    # The loop's own facts, for the pipe's fields and ledger:
                    "session_key": run.session_key,
                    "status": run.status,
                    "cost_usd": run.cost_usd,
                    "error": run.error,
                },
                "analysis": {"summary": summary, "event_type": "investigation"},
                "identity": {"session": run.session_key},
                "report": {"summary": summary, "text": run.text},
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()

        last_error = "unknown"
        for attempt, delay in enumerate(self._return_delays, start=1):
            if delay:
                await asyncio.sleep(delay)
            try:
                status = await asyncio.to_thread(self._post_return, body)
                if 200 <= status < 300:
                    run.return_status = "sent"
                    self._store.finish(run)
                    logger.info("return delivered session=%s status=%s", run.session_key, status)
                    return
                last_error = f"HTTP {status}"
            except OSError as exc:
                last_error = str(exc) or type(exc).__name__
            logger.warning(
                "return delivery attempt %s failed session=%s error=%s", attempt, run.session_key, last_error
            )
        run.return_status = f"failed: {last_error}"
        self._store.finish(run)
        await self._alarm_return_failure(run, last_error)

    async def _alarm_return_failure(self, run: Run, error: str) -> None:
        """Who watches the watchman: the pipe is the broken link right now, so
        the news travels around it — straight to a bot/collector URL. Rate
        limited, the suppressed count folds into the next message, and it
        never raises: an alarm failure must not become a delivery failure."""
        if not self._settings.alarm_url:
            return
        now = time.time()
        if now - self._alarm_last_sent < self._settings.alarm_min_interval_seconds:
            self._alarm_suppressed += 1
            return
        held = self._alarm_suppressed
        self._alarm_suppressed = 0
        self._alarm_last_sent = now
        text = f"[hookprobe] report return failed\nsession: {run.session_key}\nreason: {error[:200]}"
        if held:
            text += f"\n({held} more folded into this one during the quiet window)"
        body = json.dumps({"msg_type": "text", "content": {"text": text}}, ensure_ascii=False).encode()
        try:
            await asyncio.to_thread(self._post_alarm, body)
        except Exception:  # noqa: BLE001 — an alarm must never raise into delivery
            self._alarm_last_sent = 0.0  # let the next failure try again

    def _post_alarm(self, body: bytes) -> None:
        import urllib.request

        request = urllib.request.Request(
            self._settings.alarm_url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5):  # noqa: S310 — operator URL  # nosec B310
            pass

    def _post_return(self, body: bytes) -> int:
        import urllib.request

        headers = {"Content-Type": "application/json", **sign_timestamped(self._settings.return_secret, body)}
        request = urllib.request.Request(self._settings.return_url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310 — operator URL  # nosec B310
            return int(response.status)

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
                "inputs": dict(run.inputs),
            }
        )
