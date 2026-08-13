"""Retention: the volume is memory, not a landfill.

Case files feed the agent's episodic recall and transcripts feed follow-up
turns, so nothing is deleted by default (HOOKPROBE_RETENTION_DAYS=0). With a
window set, finished-run records and engine transcripts older than the window
are removed on startup and then daily — old enough to be beyond recall value
and beyond any realistic follow-up. Skills and the environment memory are
never touched: they are distilled knowledge, not accumulation.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

logger = logging.getLogger("hookprobe.retention")


def prune(workdir: Path, home: Path, days: int) -> int:
    """Remove case files and transcripts older than `days`. Returns the count."""
    if days <= 0:
        return 0
    cutoff = time.time() - days * 86400
    removed = 0
    targets = (
        (workdir / "results", "*.json"),
        (workdir / "audit", "*.jsonl"),
        (home / ".claude" / "projects", "**/*.jsonl"),
    )
    for root, pattern in targets:
        if not root.is_dir():
            continue
        for path in root.glob(pattern):
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError:
                continue
    if removed:
        logger.info("retention removed %s file(s) older than %s days", removed, days)
    return removed
