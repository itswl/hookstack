"""Run state: an in-memory map plus one JSON file per run.

Finished results are persisted because they are the part somebody is still
waiting to read. In-flight runs are checkpointed at spawn for a different
reason: their live state (task, events) dies with the process, but the FACT
that an investigation was running must not — a relay-born run has no poller
on the other side, only a pipe waiting for a report. After a restart the
service sweeps these orphans into failed runs that report themselves.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from hookprobe.files import atomic_write

RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"

_UNSAFE = re.compile(r"[^A-Za-z0-9._:-]")
# Long enough for any key an operator will ever read, short enough to leave
# room for the digest below inside a filesystem's 255-byte name limit.
_MAX_STEM = 200


def _filename(session_key: str) -> str:
    """One file per session key — and only ever one key per file.

    Truncation used to be the whole rule, so two keys longer than the limit that
    differed only past it named the same file: each finish() overwrote the
    other's report, and after a restart a lookup for one answered with the
    other's. The event door builds keys from an unbounded source and event id,
    which is exactly where that happens. A key that fits is still named
    literally — the case files on the volume predate this and must stay
    readable — and a longer one carries a digest of the WHOLE key, which is the
    part truncation threw away.
    """
    safe = _UNSAFE.sub("_", session_key)
    if len(safe) <= _MAX_STEM:
        return safe + ".json"
    digest = hashlib.sha256(session_key.encode("utf-8")).hexdigest()[:12]
    return f"{safe[: _MAX_STEM - len(digest) - 1]}-{digest}.json"


@dataclass(slots=True)
class Run:
    session_key: str
    run_id: str
    status: str = RUNNING
    text: str = ""
    message_count: int = 0
    error: str | None = None
    cost_usd: float | None = None
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    # Which model this session's turns are requested on.
    model: str = ""
    # Where the run came from: "" (API/UI) or "relay" (the pipe's event door —
    # these report back to the pipe when they finish).
    origin: str = ""
    # Outcome of the return delivery for relay-born runs: "", "sent", "failed: …".
    return_status: str = ""
    # What the event door knew about the alert (title/level/source/event_id) —
    # echoed back to the pipe so its probe-notify source can dress the report.
    meta: dict = field(default_factory=dict)
    # Set once the engine reports back; the handle for follow-up turns.
    engine_session_id: str | None = None
    # What auto-distill did at the end of this run: {"installed": name} or
    # {"skipped": reason}, empty when the loop is off. Recorded rather than
    # only logged — "it silently did nothing again" is the failure the feature
    # exists to end, and a log line nobody greps is how that hides.
    distilled: dict = field(default_factory=dict)
    # The message of the turn currently in flight (or the last one asked).
    current_message: str = ""
    # Finished turns, oldest first: {"message", "text", "error", "run_id",
    # "cost_usd", "finished_at", "usage", "model_usage", "duration_ms",
    # "events"}. A failed follow-up appends a turn instead of erasing the
    # answer somebody already read.
    turns: list[dict] = field(default_factory=list)
    # The in-flight turn's process feed (tool calls + narration), reset at
    # each spawn; moved onto the turn record when it finishes.
    events: list[dict] = field(default_factory=list)
    # What the engine resolved to put in front of the model for the current
    # turn — model, skill layers, subagent roles, prompt/memory digests, MCP
    # servers. Recorded because those files live on a mutable volume: without
    # it, a report cannot be explained once the volume has moved on.
    inputs: dict = field(default_factory=dict)

    @property
    def finished(self) -> bool:
        return self.status in (COMPLETED, FAILED)


class RunStore:
    def __init__(self, results_dir: Path) -> None:
        self._results_dir = results_dir
        self._runs: dict[str, Run] = {}
        self._scanned = False
        results_dir.mkdir(parents=True, exist_ok=True)

    def create(self, run: Run) -> None:
        self._runs[run.session_key] = run

    def get(self, session_key: str) -> Run | None:
        run = self._runs.get(session_key)
        if run is not None:
            return run
        return self._load(session_key)

    def finish(self, run: Run) -> None:
        run.finished_at = time.time()
        self._write(run)

    def checkpoint(self, run: Run) -> None:
        """Persist an in-flight run, so a restart can settle it instead of losing it."""
        self._write(run)

    def _write(self, run: Run) -> None:
        self._runs[run.session_key] = run
        path = self._results_dir / _filename(run.session_key)
        # Persistence is best-effort; the in-memory copy still serves.
        with contextlib.suppress(OSError):
            atomic_write(path, json.dumps(asdict(run), ensure_ascii=False).encode("utf-8"))

    def active_count(self) -> int:
        return sum(1 for run in self._runs.values() if not run.finished)

    def spend_since(self, cutoff: float) -> float:
        """Recorded model spend across all origins from turns finished after `cutoff`.

        In-flight turns have no recorded cost yet, so the figure trails reality
        by at most max_concurrent unfinished runs — the breaker reading it is
        a brake, not an invoice.
        """
        self._scan_disk_once()
        total = 0.0
        for run in self._runs.values():
            for turn in run.turns:
                finished = turn.get("finished_at")
                cost = turn.get("cost_usd")
                if finished and cost and finished >= cutoff:
                    total += float(cost)
        return total

    def unpriced_since(self, cutoff: float) -> int:
        """Turns after `cutoff` that never reported a cost — the breaker's blind spot.

        A turn the wall clock cut off spent real money the engine never got to
        report, and spend_since can only add up what was reported, so the most
        expensive failures land in the window as $0. This is the count that
        keeps that undercount from being silent: a refusal records 0.0 and is
        genuinely free, while an unreported turn records None and is not.
        """
        self._scan_disk_once()
        count = 0
        for run in self._runs.values():
            for turn in run.turns:
                finished = turn.get("finished_at")
                if finished and finished >= cutoff and turn.get("cost_usd") is None:
                    count += 1
        return count

    def cache_since(self, cutoff: float) -> tuple[int, int]:
        """(fresh_input_tokens, cache_read_tokens) across turns after `cutoff`.

        Worth watching rather than assuming. Measured on this deployment: the
        prompt an investigation carries before its alert is even mentioned is
        ~29k tokens of the harness's own system prompt and tool schemas — not
        ours to trim, since neither the prompt preset nor the allowed-tools list
        moves it. What is left is whether that fixed prefix gets reused, and this
        is the number that says so.

        Note the provider here caches implicitly: cache *writes* are always zero,
        so the honest ratio is reads over reads-plus-fresh, not reads over
        writes.
        """
        self._scan_disk_once()
        fresh = cached = 0
        for run in self._runs.values():
            for turn in run.turns:
                finished = turn.get("finished_at")
                if not finished or finished < cutoff:
                    continue
                usage = turn.get("usage") or {}
                fresh += int(usage.get("input_tokens") or 0)
                cached += int(usage.get("cache_read_input_tokens") or 0)
        return fresh, cached

    def list_runs(self, limit: int = 100) -> list[Run]:
        """Newest first, including finished runs persisted by earlier processes."""
        self._scan_disk_once()
        runs = sorted(self._runs.values(), key=lambda run: run.created_at, reverse=True)
        return runs[: max(1, limit)]

    def _scan_disk_once(self) -> None:
        # New files only ever appear through finish(), which also populates
        # the in-memory map — one scan at first listing covers restarts.
        if self._scanned:
            return
        self._scanned = True
        for path in self._results_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                key = str(data.get("session_key") or "")
                if key and key not in self._runs:
                    self._runs[key] = Run(**data)
            except (OSError, TypeError, ValueError):
                continue

    def _load(self, session_key: str) -> Run | None:
        path = self._results_dir / _filename(session_key)
        if not path.exists():
            # A case file written before _filename learned to keep long keys
            # apart sits under the old truncated name. The disk scan finds it
            # by the key recorded inside it, and runs once per process, so a
            # miss on a key that was never a run stays cheap.
            self._scan_disk_once()
            return self._runs.get(session_key)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            run = Run(**data)
        except (OSError, TypeError, ValueError):
            return None
        self._runs[session_key] = run
        return run
