"""Orchestration: accept a trigger, run the engine once, remember the outcome.

The failure shape is a deliberate choice: a run that dies (exception,
timeout, empty output) still completes the contract — isFinal true, with a
well-formed report whose root_cause says the runner failed. The caller sees
the error within one poll instead of waiting out its own timeout window.

What this module keeps is the part that has to be in one place: the task set, so
a shutdown knows what is in flight; the semaphore, so a storm queues instead of
stampeding; and the guarantee that every run reaches a final state and reports
itself. Everything a run leads to afterwards lives with the thing it is about —
hookprobe.reports writes the report-shaped refusals, hookprobe.notify carries a
relay-born report back to the pipe, hookprobe.remediation runs an approved
procedure, hookprobe.distill_loop decides what the run leaves for the next one.
Those are four different failure modes, and none of them is allowed to cost a
finished report.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import replace
from typing import Any, Protocol

import httpx

from hookprobe import actions, distill_loop, remediation, rulings, suggestions
from hookprobe.engine import EngineResult
from hookprobe.notify import ReturnDelivery
from hookprobe.reports import budget_report, failure_report
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

    async def stop(self) -> bool:
        """Ask the running turn to wind down; False if there was nothing to ask.

        Optional in practice — the fallback below cancels — but part of the
        contract because the difference is a recorded cost versus None on every
        stop and every restart, not just on a rare timeout.
        """
        ...

    def describe_inputs(self, *, resume: str | None = None) -> dict[str, Any]:
        """The prompt inputs this engine would resolve for the next turn."""
        ...


class RunBusyError(RuntimeError):
    """The session already has a turn in flight."""


class NoTurnRunningError(RuntimeError):
    """Stop was asked for, but nothing is in flight."""


class NotResumableError(ValueError):
    """The run left no engine session behind to resume."""


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
        # The family loop's last mile, and the alarm behind it.
        self._returns = ReturnDelivery(settings, store)
        # Return-retry pacing, an instance attr so tests can collapse it.
        self._return_delays: tuple[float, ...] = (0.0, 2.0, 5.0)

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
        # The previous turn's bill is not this turn's. Left in place, a follow-up
        # that died before the engine reported anything recorded the earlier
        # figure again, so a $2 turn plus a failed follow-up billed $4 to
        # window_spend() and to the session total the console shows.
        run.cost_usd = None
        run.current_message = message
        self._spawn(run, message, timeout_s, resume=run.engine_session_id)
        return run

    def stop(self, session_key: str) -> Run:
        """End the in-flight turn; it finishes as a failed turn, not a hang.

        INTERRUPT, not cancel. Cancelling the coroutine discarded the SDK's final
        message and with it the turn's cost, so every Stop an operator pressed
        recorded None — "nobody counted" — for a run the provider had billed in
        full. Asking the SDK to stop lets that message arrive.

        The cancel is still there as a fallback, on a short fuse: a turn that has
        not reached the SDK yet has nothing to interrupt, and an interrupt the SDK
        ignores must not leave a turn running forever. Answering the operator
        immediately matters more than waiting to see which path won, so the
        arrangement runs in the background and this returns now.
        """
        run = self._store.get(session_key)
        if run is None:
            raise LookupError("session not found")
        task = self._running.get(session_key)
        if run.finished or task is None:
            raise NoTurnRunningError("no turn is in flight for this session")
        self._stop_requested.add(session_key)
        closer = asyncio.create_task(self._interrupt_then_cancel(task))
        self._tasks.add(closer)
        closer.add_done_callback(self._tasks.discard)
        return run

    async def _wind_down(self, turn: asyncio.Task[Any], timeout_s: int, grace: float = 15.0) -> Any:
        """The clock ran out: ask the turn to end, and take its result if it can.

        Returning a real EngineResult here is the point. The SDK reports dollars
        only on its final message, so a turn killed outright recorded cost None —
        "nobody counted" — for the longest and therefore most expensive runs
        there are. Interrupting lets that message arrive, and the result carries
        both the bill and the SDK's own terminal_reason.

        Re-raises TimeoutError when the interrupt does not land, which is the old
        behaviour and the honest one: at that point nobody counted, and the
        unpriced_turns figure is what says so.
        """
        stop = getattr(self._engine, "stop", None)
        if stop is not None:
            try:
                if await stop():
                    settled = await asyncio.wait_for(turn, timeout=grace)
                    # It ended on OUR clock, not its own. The SDK may report a
                    # clean finish for an interrupted turn, and letting that read
                    # as success would turn "we cut it off at 900s" into "it
                    # answered" — with a truncated report standing in for one.
                    # The cost is what we came for; the verdict stays a timeout.
                    return replace(
                        settled,
                        error=settled.error or f"timed out after {timeout_s}s (interrupted; cost recorded)",
                    )
            except Exception:  # noqa: BLE001 — any failure here falls through to the kill
                logger.warning("interrupt after timeout did not settle the turn; cancelling")
        turn.cancel()
        with contextlib.suppress(BaseException):
            await turn
        raise TimeoutError

    async def _interrupt_then_cancel(self, task: asyncio.Task[Any], grace: float = 10.0) -> None:
        """Ask the SDK to stop, and cancel only if it does not.

        The grace period is what buys the accounting: winding a turn down means
        the SDK finishes its current step and emits a ResultMessage, which takes
        a moment. Cancelling immediately would be the old behaviour with extra
        steps.
        """
        interrupted = False
        stop = getattr(self._engine, "stop", None)
        if stop is not None:
            try:
                interrupted = bool(await stop())
            except Exception:  # noqa: BLE001 — the fallback below is the point
                logger.exception("engine stop() raised; cancelling instead")
        if interrupted:
            _, pending = await asyncio.wait({task}, timeout=grace)
            if not pending:
                return  # it wound down on its own, with its bill
            logger.warning("interrupt did not settle the turn in %.0fs; cancelling", grace)
        task.cancel()

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

    def sweep_interrupted_remediations(self) -> int:
        """Settle procedures a previous process died in the middle of, at startup.

        The twin of the run sweep above, for the worse case. `running` is a
        state only a live task can leave, and both approve and reject require
        `proposed` — so a restart between step 1 and step 3 stranded the row
        there for good: the remaining steps unrun, and no record anywhere that
        half a procedure had been applied to the target. "Steps 1 and 3 ran" is
        the outcome approve_remediation refuses a proposal whole to avoid; this
        is the same accident arriving by way of a dead process, and it must at
        least be written down.
        """
        settled = remediation.settle_interrupted(self._settings.workdir)
        for row in settled:
            interrupted = row.get("interrupted") or {}
            logger.warning(
                "remediation interrupted by a restart id=%s ran=%s not_run=%s",
                row.get("id"),
                len(interrupted.get("ran") or []),
                len(interrupted.get("not_run") or []),
            )
        if settled:
            self._board_changed()
        return len(settled)

    async def shutdown(self, *, grace_seconds: float = 5.0) -> int:
        """Settle background work before the process goes away; returns how many
        tasks had to be cancelled at the deadline.

        Every task here runs detached from the request that started it — a turn,
        a return delivery, an approved procedure — and nothing used to wait for
        any of them. The procedure is why this exists: its steps run
        sequentially, stop-on-first-failure, so a process that exits between
        step 1 and step 3 leaves a half-applied change and a row still saying
        `running`, which no operator action can move.

        A turn in flight is cancelled outright rather than waited for: _execute
        settles it as a failure that reports itself, which beats both the next
        boot's sweep and holding the container's stop timeout open for a
        thirty-minute investigation. The grace period is for the work with no
        such recovery — the procedure mid-sequence, and the deliveries those
        settlements just queued.
        """
        # Interrupt the turns in flight rather than killing them. This path runs
        # on every deploy, so it was the most frequent of the three that threw a
        # turn's bill away — a restart during three investigations lost three
        # costs, every time, and nothing in the ledger said a number was missing
        # rather than zero.
        stop = getattr(self._engine, "stop", None)
        if stop is not None and self._running:
            with contextlib.suppress(Exception):
                await stop()
        for task in tuple(self._running.values()):
            task.cancel()
        deadline = time.monotonic() + max(0.0, grace_seconds)
        while True:
            pending = {task for task in self._tasks if not task.done()}
            if not pending:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            logger.info("shutdown: waiting on %s background task(s)", len(pending))
            await asyncio.wait(pending, timeout=remaining)
        cancelled = 0
        for task in tuple(self._tasks):
            if not task.done():
                task.cancel()
                cancelled += 1
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)
        if cancelled:
            logger.warning("shutdown cancelled %s task(s) unfinished after %.1fs", cancelled, grace_seconds)
        return cancelled

    def budget_state(self) -> tuple[float, float] | None:
        """(spent_in_window, budget) — None when the breaker is disabled."""
        if self._settings.budget_usd <= 0:
            return None
        return self.window_spend(), self._settings.budget_usd

    def window_cache(self) -> tuple[int, int]:
        """(fresh, cached) input tokens over the budget window."""
        cutoff = time.time() - self._settings.budget_window_hours * 3600
        return self._store.cache_since(cutoff)

    def window_rulings(self) -> tuple[int, int, int]:
        """(investigations, ruled useful, ruled useless) over the budget window."""
        cutoff = time.time() - self._settings.budget_window_hours * 3600
        return self._store.rulings_since(cutoff)

    def record_ruling(self, session_key: str, ruling: str, *, actor: str = "") -> Run:
        """Write down whether a human found this investigation worth its bill.

        The cost of an investigation has always been countable and its worth was
        countable nowhere, which left the adoption question — "you want me to pay
        a model per alert?" — with a dollar figure and no answer. This is the
        other half of that figure, and it can only come from a person: no
        property of a report says whether it found the cause.

        Persisted through annotate() rather than finish(), so a ruling on an old
        investigation does not restamp it as having just finished.
        """
        if ruling not in actions.RULINGS:
            raise ValueError(f"ruling must be one of {', '.join(actions.RULINGS)}")
        run = self._store.get(session_key)
        if run is None:
            raise LookupError("session not found")
        run.ruling = ruling
        run.ruled_at = time.time()
        run.ruled_by = actor[:120]
        self._store.annotate(run)
        self._board_changed()
        logger.info("ruling recorded session=%s ruling=%s", run.session_key, ruling)
        return run

    def window_unpriced(self) -> int:
        """How many turns in the window spent money nobody could count.

        The figure below is a floor, not an invoice, and this says how far the
        floor might be off: one timed-out investigation is the most expensive
        kind of turn there is and the one the engine never gets to bill.
        """
        cutoff = time.time() - self._settings.budget_window_hours * 3600
        return self._store.unpriced_since(cutoff)

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

    def same_alert(self, source: str, title: str, window_seconds: int) -> Run | None:
        """The session already investigating this condition, if one is claimable.

        A running session claims its alert regardless of age — it is live, and
        a re-fire is information for it, not a reason to race it. A finished
        one claims re-fires for `window_seconds` after it finished, so a storm
        extends one investigation instead of funding N cold starts. Identity is
        (source, title): the same pair the case-file recall greps for.
        """
        if window_seconds <= 0 or not title:
            return None
        cutoff = time.time() - window_seconds
        best: Run | None = None
        for run in self._store.list_runs(limit=200):
            meta = run.meta or {}
            if str(meta.get("source") or "") != source or str(meta.get("title") or "") != title:
                continue
            if not run.finished:
                return run
            # Only a session that can actually be reopened is worth claiming
            # with; without an engine session there is nothing to continue.
            claimable = (run.finished_at or 0) >= cutoff and not run.error and run.engine_session_id
            if claimable and (best is None or (run.finished_at or 0) > (best.finished_at or 0)):
                best = run
        return best

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
            # A tool_done is a timing report, not a new step. Matched to its
            # streamed step by tool_use_id it becomes that step's duration; an
            # id the stream never produced is a subagent's call — the message
            # stream only carries the parent's, so this is where subagent work
            # gets into the feed at all.
            if event.get("type") == "tool_done":
                for prior in reversed(run.events):
                    if prior.get("type") == "tool_use" and prior.get("id") == event.get("id"):
                        if "ms" in event:
                            prior["ms"] = event["ms"]
                        if event.get("error"):
                            prior["error"] = True
                        self._publish(run.session_key, event)
                        return
                event = {
                    "type": "tool_use",
                    "sub": True,
                    "id": event.get("id"),
                    "name": event.get("name"),
                    "detail": event.get("detail"),
                    "ts": event["ts"],
                    **({"ms": event["ms"]} if "ms" in event else {}),
                    **({"error": True} if event.get("error") else {}),
                }
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
                    # The timeout INTERRUPTS before it cancels, for the same
                    # reason Stop does: wait_for() cancelling the coroutine threw
                    # away the SDK's final message, so the priciest failures —
                    # a turn that ran the full clock — recorded no cost at all
                    # and the budget breaker undercounted exactly them.
                    turn = asyncio.ensure_future(
                        self._engine.run(message=message, session_key=run.session_key, resume=resume, on_event=on_event)
                    )
                    try:
                        result = await asyncio.wait_for(asyncio.shield(turn), timeout=timeout_s)
                    except TimeoutError:
                        result = await self._wind_down(turn, timeout_s)
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
        # Suggestions ride the report as marker lines; lift them into the queue
        # before anything records or delivers the text.
        stripped, facts = suggestions.extract(result.text)
        run.text = stripped
        if facts:
            try:
                queued = suggestions.append(self._settings.workdir, run.session_key, facts)
                if queued:
                    run.meta["memory_suggestions"] = queued
            except OSError:
                logger.warning("could not queue memory suggestions", exc_info=True)
        # Same shape as the suggestions above and for the same reason: the agent
        # PROPOSES in its report and the service holds the credential, because
        # the agent is the component that reads attacker-influenced text.
        run.text, filed = rulings.extract(run.text)
        if filed:
            task = asyncio.create_task(self._file_rulings(run, filed))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        steps = remediation.extract(run.text)
        if steps:
            try:
                proposal_id = remediation.propose(self._settings.workdir, run.session_key, steps)
                run.meta["remediation_proposal"] = proposal_id
                logger.info("remediation proposed session=%s id=%s steps=%s", run.session_key, proposal_id, len(steps))
            except OSError:
                logger.warning("could not park the remediation proposal", exc_info=True)
        self._record_turn(run, result)
        if run.meta.get("consolidates"):
            # A consolidation run's product is a PROPOSAL beside the manifest,
            # waiting for review — and it must never itself be distilled, or
            # the loop would write runbooks about rewriting runbooks.
            distill_loop.accept_consolidation(run, result, self._settings)
        elif run.meta.get("patrol"):
            # Same rule, one category wider, and it took a real run to notice:
            # the first self-review patrol installed a runbook called
            # `patrol-self-review`. A runbook is loaded as instruction by every
            # later run, so a review OF the loop had just become part of the
            # loop — and the brief that sent it promises in its first paragraph
            # that a run of it writes nothing.
            #
            # Recorded rather than silent, because "the loop did nothing again"
            # is the failure the whole distil feature exists to end.
            run.distilled = {"skipped": "a review of the investigator is not a runbook"}
            logger.info("auto-distill skipped session=%s reason=patrol", run.session_key)
        else:
            # After the turn is recorded, because the runbook is assembled from it.
            distill_loop.auto_distill(run, result, self._settings)
            distill_loop.maybe_consolidate(run, self._settings, self._store, self.start)
        self._settle(run)
        self._schedule_return(run)
        logger.info(
            # `messages`, not `turns`: a turn is an entry in run.turns and there
            # is normally one. This is the SDK message count, and calling it
            # turns is how `turns=32294` got read as plausible for a while.
            "run completed session=%s messages=%s turns=%s cost_usd=%s",
            run.session_key,
            result.message_count,
            len(run.turns),
            result.cost_usd,
        )

    def approve_remediation(self, proposal_id: str, note: str = "") -> dict[str, Any]:
        """The operator's click, and the only path that runs anything. The gate
        checks and the execution are hookprobe.remediation's; what belongs here
        is the task the sequence runs in, because shutdown has to wait for it."""
        row = remediation.approve(
            self._settings.workdir,
            proposal_id,
            allowlist=self._settings.remediation_allowlist,
            note=note,
        )
        task = asyncio.create_task(self._apply_remediation(row))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        self._board_changed()
        return row

    def reject_remediation(self, proposal_id: str) -> dict[str, Any]:
        row = remediation.reject(self._settings.workdir, proposal_id)
        self._board_changed()
        return row

    async def _apply_remediation(self, row: dict[str, Any]) -> None:
        await remediation.execute(
            self._settings.workdir,
            row,
            bash_timeout_ms=self._settings.bash_timeout_ms,
            # The second gate needs the file, not the patterns read at the click:
            # an operator narrowing it mid-procedure should stop what has not run.
            allowlist=self._settings.remediation_allowlist,
        )
        self._board_changed()

    def _fail(self, run: Run, reason: str, result: EngineResult | None = None) -> None:
        run.status = FAILED
        run.error = reason
        run.text = failure_report(reason)
        # A failure that got a result still knows its bill; one that was cut off
        # mid-turn — wall clock, crash, Stop — never will, because the engine
        # reports dollars only with its result. Recording None there is the
        # honest answer, and _record_turn says why it is not the same as $0.
        if result is not None:
            run.cost_usd = result.cost_usd
        self._record_turn(run, result)
        self._settle(run)
        self._schedule_return(run)
        logger.warning("run failed session=%s reason=%s", run.session_key, reason)

    async def _file_rulings(self, run: Run, filed: list[dict[str, Any]]) -> None:
        """Post each ruling to the judge. Detached, and never costs the report.

        No retry. A ruling is a standing read of evidence that the next patrol
        will produce again next week, so a lost one costs a week rather than a
        fact — and a retry queue for it would be more machinery than the thing is
        worth. The failure is logged with the identity so it is greppable.
        """
        settings = self._settings
        if not settings.ruling_url or not settings.ruling_secret:
            logger.info("rulings not configured; %s dropped for %s", len(filed), run.session_key)
            return
        sent = 0
        async with httpx.AsyncClient(timeout=10.0) as client:
            for body, headers in rulings.payloads(filed, model=run.model, secret=settings.ruling_secret):
                try:
                    answer = await client.post(
                        settings.ruling_url, content=body, headers={**headers, "content-type": "application/json"}
                    )
                    if answer.status_code >= 400:
                        logger.warning("ruling refused status=%s body=%s", answer.status_code, answer.text[:200])
                        continue
                    sent += 1
                except httpx.HTTPError as exc:
                    logger.warning("ruling could not be delivered: %s", exc)
        run.meta["ai_rulings"] = sent
        logger.info("rulings filed session=%s sent=%s of %s", run.session_key, sent, len(filed))

    def _schedule_return(self, run: Run) -> None:
        """The family loop: relay-born runs report back to the pipe. Detached,
        because nobody is waiting on this side — see hookprobe.notify.

        `_meta.notify` opts a run in that the relay did not send. The guard used
        to be origin alone, which is right for the two callers that existed: a
        relay-born run reports back, and a platform-born one is POLLED at
        /final, so returning as well would deliver it twice.

        A patrol is a third case and had neither. Nothing polls it — the crontab
        that fired it is long gone — so its report reached a JSON file on the
        volume and stopped there. Three verified patrol runs cost $2.20 and were
        read by nobody but me, over SSH. A scheduled report with no delivery is
        just a slower way of spending money.
        """
        if not self._settings.return_url:
            return
        if run.origin != "relay" and not run.meta.get("notify"):
            return
        task = asyncio.create_task(self._returns.deliver(run, self._return_delays))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _record_turn(self, run: Run, result: EngineResult | None) -> None:
        run.turns.append(
            {
                "message": run.current_message,
                "text": run.text,
                "error": run.error,
                "run_id": run.run_id,
                # None means "nobody counted", 0.0 means "cost nothing" — a
                # refusal is free, a run the wall clock cut off is not, and the
                # ledger must not read the second as the first. window_spend()
                # can only add up what was reported; window_unpriced() says how
                # many turns are missing from it.
                "cost_usd": run.cost_usd,
                "finished_at": time.time(),
                "usage": result.usage if result else None,
                "model_usage": result.model_usage if result else None,
                "duration_ms": result.duration_ms if result else None,
                "events": list(run.events),
                "inputs": dict(run.inputs),
                # Empty on every healthy run. Stored next to the inputs it is
                # about, so a report and the evidence that the run edited what
                # produced it are never more than one record apart.
                "input_changes": list(result.input_changes) if result else [],
            }
        )
