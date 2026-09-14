"""The families this node has no instrument for are declined at the door.

Measured on the deployment this was built for. The platform upstream excluded
four alert families from investigation on 2026-09-01 — mail delivery, the
message broker, cache metrics, managed-database metrics — because the
investigator holds no credential for the cloud account their data lives in.
Three days later the pipe's own escalation leg started funding them again on
the judge's verdict alone: twenty investigations of the mail family in one
week, 70% of the investigator's spend, every one ending in a report that could
only restate the alert. A judge saying "high" is not evidence the investigator
can LOOK; that is a fact about the investigator, and it belongs in a file the
operator writes for it.

The list is a file of full-match regexes over alert titles, hot-read on every
event like the remediation allowlist. Unlike the allowlist it fails OPEN on a
bad line: a typo in a list whose job is to save money must not silently stop
every investigation, so an unparseable pattern is logged and skipped, never
read as "decline everything".
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("hookprobe.decline")


def load_patterns(path: Path | None) -> list[re.Pattern[str]]:
    """The list as it stands right now. Missing file or unset knob: nothing."""
    if path is None:
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    patterns: list[re.Pattern[str]] = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            patterns.append(re.compile(line))
        except re.error as exc:
            logger.warning("decline list %s line %d is not a regex and is ignored: %s", path, lineno, exc)
    return patterns


def decline_reason(path: Path | None, title: str) -> str:
    """The pattern that declines `title`, or "". Full-match, never search: a
    pattern has to name the whole title, so `SES` alone declines nothing and
    `\\[SES\\].*` declines the family it spells out."""
    for pattern in load_patterns(path):
        if pattern.fullmatch(title):
            return pattern.pattern
    return ""


_LEDGER = "declines.jsonl"


def record(workdir: Path, title: str, pattern: str, *, at: float | None = None) -> None:
    """One line per decline — when, which title, which pattern. Append-only and
    tiny (a few lines a day), so the weekly page can say what the list saved
    instead of the saving being a log line nobody greps. Best effort: a door
    that could not write its ledger still declines."""
    row = {"at": round(time.time() if at is None else at, 3), "title": title[:200], "pattern": pattern[:200]}
    try:
        with (workdir / _LEDGER).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        logger.warning("could not record a decline in %s", workdir / _LEDGER, exc_info=True)


def tally(workdir: Path, path: Path | None, *, since: float) -> dict[str, Any]:
    """What the list did inside a window, three-valued on purpose.

    `configured` false means there is no list: "declined 0" then is not a
    result, it is the absence of a mechanism, and the page must not read it as
    "the list found nothing". `patterns` is how many lines the list has right
    now, so a list that is set but empty (or all typos) is visible too.
    """
    patterns = load_patterns(path)
    declined = 0
    conditions: set[str] = set()
    by_pattern: dict[str, int] = {}
    try:
        lines = (workdir / _LEDGER).read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or float(row.get("at") or 0) < since:
            continue
        declined += 1
        conditions.add(str(row.get("title") or ""))
        key = str(row.get("pattern") or "")
        by_pattern[key] = by_pattern.get(key, 0) + 1
    return {
        "configured": path is not None,
        "patterns": len(patterns),
        "declined": declined,
        "conditions": len(conditions),
        "by_pattern": by_pattern,
    }
