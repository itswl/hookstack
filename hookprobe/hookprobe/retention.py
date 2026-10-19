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
import os
import shutil
import time
from pathlib import Path

logger = logging.getLogger("hookprobe.retention")

# Where the agent's subprocesses put temporary files (engine._subprocess_env
# sets TMPDIR to it), and where the CLI writes its own MCP transport logs.
SCRATCH = "scratch"
CLI_CACHE = Path(".cache") / "claude-cli-nodejs"


def prune(workdir: Path, home: Path, days: int) -> int:
    """Remove case files and transcripts older than `days`. Returns the count."""
    if days <= 0:
        return 0
    cutoff = time.time() - days * 86400
    removed = 0
    targets = (
        (workdir / "results", "*.json"),
        (workdir / "audit", "*.jsonl"),
        (workdir / "telemetry", "*.jsonl"),
        # One row per card button press, kept only so a redelivery of that press
        # can be answered instead of repeated. An IM platform retries within
        # seconds, so a row this old is bookkeeping nobody will read again —
        # unlike the case files above, which are the agent's episodic memory.
        (workdir / "actions", "*.json"),
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


def _newest_write(path: Path) -> float | None:
    """When anything under `path` was last written; None if any of it is unreadable."""
    try:
        newest = path.lstat().st_mtime
    except OSError:
        return None
    if path.is_dir() and not path.is_symlink():
        failed: list[OSError] = []
        for root, dirs, files in os.walk(path, onerror=failed.append):
            for name in dirs + files:
                try:
                    newest = max(newest, os.lstat(os.path.join(root, name)).st_mtime)
                except OSError:
                    return None
        if failed:
            return None
    return newest


def prune_scratch(workdir: Path, home: Path, days: int) -> int:
    """Remove scratch entries and CLI transport logs older than `days`.

    Two things that are not the agent's memory and never were: `{workdir}/scratch`,
    the TMPDIR every agent subprocess is given, where a download an investigation
    made lands; and `$HOME/.cache/claude-cli-nodejs`, the CLI's own MCP transport
    logs, which carry whatever a tool returned — colleagues' messages included —
    and which nothing reads back. Measured 2026-10-19 on two nodes: those logs
    had grown since 2026-09-04 with `prune` above never naming them, and a
    sibling deployment had found a day-old scratch holding other teams' configs.
    A window of their own because the default differs: case files are recall and
    default to kept; these default to a week.

    A scratch entry is a top-level file or directory. A directory is old only
    when NOTHING under it was written inside the window, so a job still writing
    into its own folder is left alone; anything unreadable is left alone too.
    """
    if days <= 0:
        return 0
    cutoff = time.time() - days * 86400
    removed = 0
    scratch = workdir / SCRATCH
    try:
        entries = sorted(scratch.iterdir()) if scratch.is_dir() else []
    except OSError:
        entries = []
    for entry in entries:
        newest = _newest_write(entry)
        if newest is None or newest >= cutoff:
            continue
        try:
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink()
            removed += 1
        except OSError:
            continue
    cache = home / CLI_CACHE
    if cache.is_dir():
        for path in cache.glob("**/*"):
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError:
                continue
    if removed:
        logger.info("scratch retention removed %s entr(ies) older than %s days", removed, days)
    return removed
