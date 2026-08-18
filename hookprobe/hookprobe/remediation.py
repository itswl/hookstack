"""From "here is what I would do" to "done, with receipts" — one gate at a time.

The investigator is read-only by design, and stays read-only: this module
never gives the AGENT a pen. A report may end with a fenced ```remediation
block — structured steps, each with a command, a risk and a rollback — and the
SERVICE lifts that block into a proposal file. From there every transition is
an operator's:

    proposed ──(operator approves)──► running ──► executed | failed
        └─────(operator rejects)───► rejected

Execution is service-side and dumb on purpose: the approved commands run
exactly as written, sequentially, stop-on-first-failure, each output captured
and each command appended to the same audit JSONL the agent's tools write to.
No agent in the loop at execution time — an agent that "adapts" an approved
command is executing something nobody approved.

The gate that makes any of this runnable is the allowlist file
(HOOKPROBE_REMEDIATION_ALLOWLIST): one regex per line, hot-read at execution
time, deny-by-default. No file, no execution — proposals still collect, which
is the shipping default. The read-only bash guard's deny list deliberately
does NOT apply here: remediation exists to do the mutations that guard blocks,
and its gate is the operator's allowlist plus the operator's click, not a
regex that errs toward blocking investigations.

The proposals directory is on the input guard's protected list: the agent
proposes THROUGH its report, so a direct write could only mean forging a
proposal's provenance. (A bash write around that guard still produces only a
`proposed` row — approval and the allowlist stand between it and execution.)
"""

from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

DIRNAME = "remediation"

_BLOCK = re.compile(r"```remediation\s*\n(.*?)```", re.DOTALL)
_MAX_STEPS = 5
_RISKS = ("low", "medium", "high")


def extract(text: str) -> list[dict[str, Any]]:
    """The steps a report proposed, validated — or nothing.

    The block stays in the report on purpose (unlike memory-suggestion
    markers): remediation advice is content a human reading the case file
    wants; this only lifts a structured copy.
    """
    match = _BLOCK.search(text or "")
    if not match:
        return []
    try:
        raw = json.loads(match.group(1))
    except ValueError:
        return []
    if not isinstance(raw, list):
        return []
    steps: list[dict[str, Any]] = []
    for entry in raw[:_MAX_STEPS]:
        if not isinstance(entry, dict):
            continue
        command = str(entry.get("command") or "").strip()
        action = str(entry.get("action") or "").strip()
        if not command or not action:
            continue
        risk = str(entry.get("risk") or "medium").strip().lower()
        steps.append(
            {
                "action": action[:200],
                "command": command[:500],
                "target": str(entry.get("target") or "").strip()[:200],
                "risk": risk if risk in _RISKS else "medium",
                "rollback": str(entry.get("rollback") or "").strip()[:500],
            }
        )
    return steps


def propose(workdir: Path, session_key: str, steps: list[dict[str, Any]]) -> str:
    """Park a run's steps as a proposal; returns its id."""
    directory = workdir / DIRNAME
    directory.mkdir(parents=True, exist_ok=True)
    proposal_id = uuid.uuid4().hex[:10]
    _write(
        directory / f"{proposal_id}.json",
        {
            "id": proposal_id,
            "session_key": session_key,
            "created_at": round(time.time(), 3),
            "status": "proposed",
            "steps": steps,
            "results": [],
        },
    )
    return proposal_id


def load(workdir: Path, proposal_id: str) -> dict[str, Any] | None:
    path = workdir / DIRNAME / f"{proposal_id}.json"
    if not re.fullmatch(r"[0-9a-f]{10}", proposal_id) or not path.is_file():
        return None
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return row if isinstance(row, dict) else None


def list_all(workdir: Path, limit: int = 100) -> list[dict[str, Any]]:
    directory = workdir / DIRNAME
    rows: list[dict[str, Any]] = []
    try:
        paths = sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return []
    for path in paths[: max(1, limit)]:
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def save(workdir: Path, row: dict[str, Any]) -> None:
    _write(workdir / DIRNAME / f"{row['id']}.json", row)


def _write(path: Path, row: dict[str, Any]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(row, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def allowlist_patterns(path: Path | None) -> list[str]:
    """Hot-read at execution time: editing the file needs no restart."""
    if path is None:
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]


def deny_reason(command: str, patterns: list[str]) -> str | None:
    """Why this command may not run — deny by default, allow by explicit match."""
    if not patterns:
        return "no allowlist configured (HOOKPROBE_REMEDIATION_ALLOWLIST); proposals collect, nothing executes"
    for pattern in patterns:
        try:
            if re.fullmatch(pattern, command):
                return None
        except re.error:
            continue  # a broken pattern must fail closed, not open
    return "command matches no allowlist pattern"
