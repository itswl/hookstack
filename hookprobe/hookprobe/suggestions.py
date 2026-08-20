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
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any

from hookprobe.files import atomic_write, locked

logger = logging.getLogger("hookprobe.suggestions")

QUEUE_FILE = "memory-suggestions.jsonl"
HEADING = "## Learned from investigations"
# A DIFFERENT heading for what nobody approved, because the memory file must not
# claim a person signed off on a line no person read. It also does a second job:
# it frames these as observations rather than standing instructions, which is
# the demotion that makes applying them unattended defensible at all.
HEADING_UNVERIFIED = "## Observed by investigations (unverified, applied automatically)"

_MARKER = re.compile(r"^\s*MEMORY-SUGGESTION:\s*(.+?)\s*$", re.MULTILINE)
_PER_RUN = 3  # a report is one investigation's view; three facts is already generous
_QUEUE_CAP = 50  # a full queue refuses new entries rather than evicting unread ones


def extract(text: str) -> tuple[str, list[str]]:
    """(report without the marker lines, the suggested facts).

    Stripped because the report travels to notification channels, and a
    machine-to-service line rendered into a Feishu card is noise; the fact is
    preserved in the queue, which is its actual destination.

    A note is left where the lines were, because removing them silently makes
    the report lie by omission. The first self-review patrol wrote its report
    under a heading "Proposed memory (one line)", the marker under it was
    lifted, and what remained was a bare heading — which reads exactly like a
    model that ignored the instruction or an answer that was truncated. It cost
    a wrong diagnosis and a commit message that named the wrong cause. The note
    is prose, so a Feishu card carrying it says something true to a person.
    """
    lines = [match.strip()[:300] for match in _MARKER.findall(text or "")]
    unique: list[str] = []
    for line in lines:
        if line and line not in unique:
            unique.append(line)
    kept = unique[:_PER_RUN]
    if not kept:
        return text or "", []

    # One note for the whole report, at the position of the first marker, so it
    # stays under whatever heading the run wrote it under.
    note = f"_({len(kept)} memory suggestion{'' if len(kept) == 1 else 's'} queued for review.)_"
    replaced = False

    def _once(_match: re.Match[str]) -> str:
        nonlocal replaced
        if replaced:
            return ""
        replaced = True
        return note

    stripped = _MARKER.sub(_once, text or "")
    return stripped.rstrip() + ("\n" if text and text.endswith("\n") else ""), kept


# What a line must not contain to be applied without a person reading it.
#
# The danger was never the sentence, it is that CLAUDE.md is loaded as
# INSTRUCTION by every later run, and the run that proposes has been reading
# alert payloads an attacker can influence. So the bar is not "is this true" —
# nothing here can know that — it is "can this line act".
#
# `MEMORY-SUGGESTION: gateway-2's Sunday spike is the reporting batch job` is a
# fact about topology. `...so it is safe to ignore all gateway-2 alerts` is the
# same sentence carrying an instruction, and that second clause is what these
# patterns exist to catch. A line that trips any of them is not rejected — it
# stays queued for a human, which is what the queue was always for.
_UNSAFE = (
    # Telling a later run what to do, or not do.
    (re.compile(r"\b(?:always|never|ignore|skip|suppress|disable|do not|don't|must|should)\b", re.I), "an instruction"),
    (re.compile(r"\b(?:safe to|no need to|feel free|you (?:can|should|must))\b", re.I), "permission-granting"),
    # Addressing the reader is how a fact becomes a directive.
    (re.compile(r"\b(?:you|your)\b", re.I), "second person"),
    # Anything executable or fetchable. A URL in standing instruction is a
    # request the next run may make on the proposer's behalf.
    (re.compile(r"https?://|`|\$\(|\|\||&&|;\s*\w+\s|\bcurl\b|\bbash\b|\bsh\b\s|\brm\b", re.I), "executable or a URL"),
    # Prompt-shaped scaffolding: headings, roles, fenced blocks.
    (re.compile(r"^#|^\s*[-*]\s|```|\bsystem\s*:|\bassistant\s*:|\buser\s*:", re.I | re.M), "prompt scaffolding"),
)
_APPLY_MAX = 200


def unsafe_reason(fact: str) -> str | None:
    """Why this line may not be applied unattended, or None if it may.

    Deliberately conservative and deliberately dumb. It cannot tell a true fact
    from a false one, so it does not try; it only refuses lines shaped like
    something that could act. Everything it refuses stays in the queue.
    """
    line = " ".join(fact.split())
    if not line:
        return "empty"
    if len(line) > _APPLY_MAX:
        return f"longer than {_APPLY_MAX} characters"
    if "\n" in fact.strip():
        return "more than one line"
    for pattern, why in _UNSAFE:
        if pattern.search(line):
            return why
    return None


def append(workdir: Path, session_key: str, facts: list[str], *, apply_safe: bool = False) -> dict[str, int]:
    """File what a run suggested. Returns {"applied": n, "queued": n}.

    `apply_safe` is the answer to a deployment where nobody presses accept. The
    queue was the right design for an attended system and a dead end for this
    one: one suggestion sat `open` from the moment it was made, and the weekly
    self-review's most useful output became a report that nothing had been
    accepted again.

    So a line that `unsafe_reason` cannot object to is applied, under a heading
    that says nobody approved it. Everything else stays queued, which is what
    the queue was always for. The bar is shape, never truth — see `_UNSAFE`.
    """
    if not facts:
        return {"applied": 0, "queued": 0}
    applied = 0
    if apply_safe:
        remaining: list[str] = []
        for fact in facts:
            reason = unsafe_reason(fact)
            if reason is None:
                _write_memory(workdir / "CLAUDE.md", fact, heading=HEADING_UNVERIFIED)
                applied += 1
            else:
                logger.info("memory suggestion queued for a human: %s", reason)
                remaining.append(fact)
        facts = remaining
        if not facts:
            return {"applied": applied, "queued": 0}
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
    return {"applied": applied, "queued": queued}


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
        _write_memory(memory, str(target["line"]), heading=HEADING)
    return target


def _write_memory(memory: Path, line: str, *, heading: str) -> None:
    """Append one line under one heading. Locked: this is a read-append-write and
    the operator's own editor writes the same file through PUT /v1/memory."""
    with locked(memory):
        try:
            text = memory.read_text(encoding="utf-8")
        except OSError:
            text = ""
        if heading not in text:
            text = (text.rstrip() + f"\n\n{heading}\n\n") if text.strip() else f"{heading}\n\n"
        # Under the RIGHT heading, not merely at the end: two headings exist so
        # provenance survives, and appending to the tail would file an unverified
        # line under whichever heading happened to be last.
        head, _, rest = text.partition(heading)
        body, sep, tail = rest.partition("\n## ")
        body = body.rstrip() + f"\n- {line}\n"
        atomic_write(memory, (head + heading + body + (sep + tail if sep else "")).encode("utf-8"))
