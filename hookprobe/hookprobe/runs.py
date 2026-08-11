"""Run state: an in-memory map plus one JSON file per finished run.

In-flight runs die with the process — WebhookWise's poller then sees a 404,
fails the record per its own policy, and the operator can re-trigger from
the dashboard. Finished results are persisted to disk because they are the
part somebody is still waiting to read.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"

_UNSAFE = re.compile(r"[^A-Za-z0-9._:-]")


def _filename(session_key: str) -> str:
    return _UNSAFE.sub("_", session_key)[:200] + ".json"


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
    # Set once the engine reports back; the handle for follow-up turns.
    engine_session_id: str | None = None
    # The message of the turn currently in flight (or the last one asked).
    current_message: str = ""
    # Finished turns, oldest first: {"message", "text", "error", "run_id",
    # "cost_usd", "finished_at"}. A failed follow-up appends a turn instead
    # of erasing the answer somebody already read.
    turns: list[dict] = field(default_factory=list)

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
        self._runs[run.session_key] = run
        path = self._results_dir / _filename(run.session_key)
        tmp = path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(asdict(run), ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)
        except OSError:
            # Persistence is best-effort; the in-memory copy still serves.
            tmp.unlink(missing_ok=True)

    def active_count(self) -> int:
        return sum(1 for run in self._runs.values() if not run.finished)

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
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            run = Run(**data)
        except (OSError, TypeError, ValueError):
            return None
        self._runs[session_key] = run
        return run
