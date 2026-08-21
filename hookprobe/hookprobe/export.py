"""Runbooks, packaged for somebody who is not this deployment.

The runbooks distilled here are the only thing this service produces that gains
value over time and travels: "DatasourceNoData with value=null is a NoData state,
not a reading of zero" is true wherever Grafana runs. That is worth handing to
someone.

WHAT MAKES THIS DIFFERENT FROM `GET /v1/skills/{name}`

That endpoint serves a runbook to its own operator. This one prepares one to
LEAVE, and a real manifest from production says why that is not the same request:

    examplecorp 平台（grafana.examplecorp.local）SES 账户信誉永久退信率达到 10.3%
    session `hook:deep-analysis:grafana:SESSION-UUID`

A brand, an internal hostname, a session key, an exact figure. None of it is a
secret and all of it is somebody's business. Publishing is not undoable.

THE CUT IS STRUCTURAL, NOT A SCRUBBER

A regex pass over model prose would have to be trusted, and a sanitiser that
misses one thing is worse than one that never claimed to sanitise. So the split
follows a boundary the file already has: `distill.CASES_MARKER` separates the
PROCEDURE a consolidation wrote from the CASES it was distilled from, and every
example above lives on the cases side. Cases are dropped; the procedure travels.

Credentials are the one exception, redacted rather than reported, because a miss
there is severe rather than embarrassing. Everything else identifying is REPORTED
against the exported text, for a person to read before publishing. This module
does not decide that anything is safe to publish. It decides what is portable and
says what it noticed.

CONSOLIDATED ONLY, WHICH IS FEWER THAN YOU THINK

An auto-written runbook is a pile of cases with a heading — on production, six
lines before the marker and nothing after it but sessions. There is no procedure
in it yet, so there is nothing to export; exporting the shell would ship a file
whose only real content had just been removed. Those are named in `omitted` with
their case count, which doubles as the list of runbooks worth consolidating.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from hookprobe.distill import CASES_MARKER, case_count
from hookprobe.redact import redact

# Reported, never rewritten. Each one is a thing a person might legitimately want
# to publish and might not — a hostname in a public runbook could be an example
# or an internal name, and only somebody who knows the deployment can say.
_IDENTIFYING = (
    ("session key", re.compile(r"\b(?:hook|probe|patrol|consolidate):[\w.:-]{4,}")),
    ("uuid", re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)),
    ("internal hostname", re.compile(r"\b[\w-]+(?:\.[\w-]+)*\.(?:local|internal|lan|corp|svc)\b", re.I)),
    ("ip address", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("url", re.compile(r"https?://[^\s)\"'>]+")),
)
_FINDING_MAX = 6  # per kind per skill; a list nobody reads is not a warning


def _procedure(text: str) -> str:
    """Everything a consolidation wrote, and nothing it was written FROM.

    Cut at the marker rather than at a heading: headings are model-authored and
    in whatever language the alerts are, while the marker is written by
    distill.py and is the same in every file.
    """
    head = text.split(CASES_MARKER, 1)[0].rstrip()
    # The Investigations heading introduces the cases, so it leaves with them.
    return re.sub(r"\n#+\s*Investigations\s*$", "", head).rstrip() + "\n"


def _is_consolidated(text: str) -> bool:
    """Has anything been written here beyond frontmatter and a title?

    Measured as prose lines in the procedure that are not the title or the
    frontmatter — an auto-written runbook has none, a consolidated one has the
    steps somebody would actually follow.
    """
    body = _procedure(text)
    _, _, after = body.partition("---\n")
    _, _, after = after.partition("---\n")
    return any(line.strip() and not line.startswith("#") for line in after.splitlines())


def findings(name: str, text: str) -> list[dict[str, Any]]:
    """What in this text looks like it belongs to one deployment."""
    out: list[dict[str, Any]] = []
    for kind, pattern in _IDENTIFYING:
        hits: list[str] = []
        for match in pattern.findall(text):
            value = match if isinstance(match, str) else match[0]
            if value not in hits:
                hits.append(value)
        if hits:
            out.append({"skill": name, "kind": kind, "count": len(hits), "examples": hits[:_FINDING_MAX]})
    return out


def bundle(skills_dir: Path) -> dict[str, Any]:
    """Everything portable, everything skipped, and what to read before sharing.

    JSON rather than a tarball on purpose: the risk here is publishing something
    by accident, and a structure an operator can read in a terminal before it
    becomes files is the safer default. Turning it into directories is three
    lines of script on the other side, once somebody has looked.
    """
    exported: list[dict[str, Any]] = []
    omitted: list[dict[str, Any]] = []
    spotted: list[dict[str, Any]] = []

    for manifest in sorted(skills_dir.glob("*/SKILL.md")):
        name = manifest.parent.name
        try:
            text = manifest.read_text(encoding="utf-8")
        except OSError as exc:
            omitted.append({"name": name, "reason": f"unreadable: {exc}"})
            continue
        cases = case_count(text)
        if not _is_consolidated(text):
            omitted.append(
                {
                    "name": name,
                    "cases": cases,
                    "reason": "not consolidated — the cases are all it has, and they do not travel",
                }
            )
            continue
        content = redact(_procedure(text))
        exported.append({"name": name, "cases_dropped": cases, "content": content})
        spotted.extend(findings(name, content))

    return {
        "exported": exported,
        "omitted": omitted,
        # Named `review` rather than `warnings`: nothing here is necessarily
        # wrong, and this module is not entitled to call anything safe.
        "review": spotted,
        "note": (
            "Procedures only — every case was dropped, because that is where the session keys, "
            "hostnames and figures live. Credentials are redacted; anything else identifying is "
            "listed under `review` for you to read before this leaves the building."
        ),
    }
