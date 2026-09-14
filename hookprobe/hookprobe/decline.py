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

import logging
import re
from pathlib import Path

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
