"""Environment facts an investigation wants remembered — queued, never written.

The environment memory (CLAUDE.md) steers every run, which is exactly why runs
cannot write it (hookprobe.inputs). But investigations keep LEARNING things
that belong there — "the overlay and /data are one filesystem", "gateway-2's
Sunday spike is a batch job" — and the only path for such a fact used to be an
operator noticing it in a report and retyping it.

So the family-door prompt invites the run to end its report with
`MEMORY-SUGGESTION: <one line>`. The service lifts those lines out of the
report (the channels should not carry them), parks them in a queue file, and
the memory page offers accept — which appends to CLAUDE.md under one heading —
or dismiss. The agent's tools cannot touch the queue file (it is on the input
guard's list); a run that shells around that guard can at worst stuff the
queue with garbage an operator will dismiss, never write memory itself.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

from hookprobe.files import atomic_write, locked

QUEUE_FILE = "memory-suggestions.jsonl"
HEADING = "## Learned from investigations"

_MARKER = re.compile(r"^\s*MEMORY-SUGGESTION:\s*(.+?)\s*$", re.MULTILINE)
_PER_RUN = 3  # a report is one investigation's view; three facts is already generous
_QUEUE_CAP = 50  # a full queue refuses new entries rather than evicting unread ones


def extract(text: str) -> tuple[str, list[str]]:
    """(report without the marker lines, the suggested facts).

    Stripped because the report travels to notification channels, and a
    machine-to-service line rendered into a Feishu card is noise; the fact is
    preserved in the queue, which is its actual destination.
    """
    lines = [match.strip()[:300] for match in _MARKER.findall(text or "")]
    unique: list[str] = []
    for line in lines:
        if line and line not in unique:
            unique.append(line)
    return _MARKER.sub("", text or "").rstrip() + ("\n" if text and text.endswith("\n") else ""), unique[:_PER_RUN]


def append(workdir: Path, session_key: str, facts: list[str]) -> int:
    """Queue what a run suggested; returns how many were actually queued."""
    if not facts:
        return 0
    path = workdir / QUEUE_FILE
    # Locked across the count AND the append: the cap is read from the file, so
    # two runs appending at once would each see the same "open_now" and together
    # sail past it.
    with locked(path):
        open_now = sum(1 for row in load(workdir) if row.get("status") == "open")
        queued = 0
        with path.open("a", encoding="utf-8") as handle:
            for fact in facts:
                if open_now + queued >= _QUEUE_CAP:
                    break
                handle.write(
                    json.dumps(
                        {
                            "id": uuid.uuid4().hex[:10],
                            "ts": round(time.time(), 3),
                            "session_key": session_key,
                            "line": fact,
                            "status": "open",
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                queued += 1
    return queued


def load(workdir: Path) -> list[dict[str, Any]]:
    path = workdir / QUEUE_FILE
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    except OSError:
        return []
    return rows


def resolve(workdir: Path, suggestion_id: str, *, accept: bool) -> dict[str, Any] | None:
    """Close one suggestion; on accept, the fact lands in CLAUDE.md.

    Appended under one heading so accepted facts stay recognisable as
    machine-suggested, operator-approved — distinct from what the operator
    wrote unprompted.
    """
    path = workdir / QUEUE_FILE
    # The whole read-modify-write under one lock. This rewrites EVERY row from a
    # copy read a moment earlier, so an append landing in between was simply
    # dropped — the loudest version of the lost update this service can have,
    # because what goes missing is a fact a run asked a human to remember.
    with locked(path):
        rows = load(workdir)
        target = next((row for row in rows if row.get("id") == suggestion_id and row.get("status") == "open"), None)
        if target is None:
            return None
        target["status"] = "accepted" if accept else "dismissed"
        target["resolved_at"] = round(time.time(), 3)
        atomic_write(path, "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows).encode("utf-8"))
    if accept:
        memory = workdir / "CLAUDE.md"
        # A second lock, on the memory file: this is another read-append-write,
        # and the operator's own editor writes the same file through PUT /v1/memory.
        with locked(memory):
            try:
                text = memory.read_text(encoding="utf-8")
            except OSError:
                text = ""
            if HEADING not in text:
                text = (text.rstrip() + f"\n\n{HEADING}\n\n") if text.strip() else f"{HEADING}\n\n"
            text = text.rstrip() + f"\n- {target['line']}\n"
            atomic_write(memory, text.encode("utf-8"))
    return target
