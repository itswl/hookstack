"""Retrospective rulings the investigator proposes and the SERVICE files.

`ruled` on the judge's ledger is 0 and will stay 0 — nobody presses the buttons
on the cards. So a patrol reads the case files and rules on the conditions it can
defend a verdict on, and that verdict lands in `ai_rulings`, a table it is
impossible for it to confuse with `mattered`.

WHY THE AGENT DOES NOT POST THIS ITSELF

The obvious build is a line in the brief telling it to curl the judge, with
HOOKJUDGE_RULING_SECRET in its environment. That contradicts the posture the rest
of this service is built on: the agent cannot write its own inputs, cannot reach
`.claude/`, cannot touch the memory queue — the service does those things on its
behalf, because the agent is the component that reads attacker-influenced alert
text and runs tools over it. Handing that component a reusable signing key for a
sibling service's ledger undoes the argument.

So the agent proposes in its report, the same way it proposes memory
(`MEMORY-SUGGESTION:`) and procedures (a remediation block), and the service
holds the credential. A prompt-injected run can still file a wrong ruling — that
is a wrong number in a ledger, visible and overwritable, which is the blast
radius this whole gate was judged acceptable at. What it cannot do is take the
key somewhere else.

WHY A SEPARATE SECRET FROM THE INGEST ONE

`HOOKJUDGE_INGEST_SECRET` also opens `/events`. A component able to sign for that
door can forge judgements. This one signs for `/rulings/ai` and nothing else.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from hookprobe.wire import sign_timestamped

logger = logging.getLogger("hookprobe.rulings")

# One JSON object per line, so a report that rules on three conditions files
# three. Kept deliberately close to the MEMORY-SUGGESTION shape: an operator
# reading a brief should not have to learn a second convention.
# Matches the MARKER, not a well-formed payload. A pattern that required valid
# JSON left `AI-RULING: not json at all` sitting in the report, which then rode
# out to a chat card — the line is stripped either way and only the parseable
# ones are filed.
_MARKER = re.compile(r"^[ \t]*AI-RULING:[ \t]*(.*)$", re.MULTILINE)
_PER_RUN = 5  # noisiest[] is capped at five conditions; a sixth ruling is a bug
VERDICTS = ("worth_it", "not_worth_it")
_WHY_MAX = 600


def extract(text: str) -> tuple[str, list[dict[str, Any]]]:
    """(report without the marker lines, the rulings worth filing).

    Stripped for the same reason MEMORY-SUGGESTION is: the report travels to a
    chat card, and a machine-to-service line rendered there is noise. A note is
    left where they were, because a section that silently loses its contents
    reads exactly like a model that ignored the instruction — that mistake has
    already been made once in this repository.

    Malformed rulings are DROPPED here rather than sent for the judge to refuse.
    A 400 from the far end arrives in a log nobody reads, on a schedule; a report
    that says it filed two and filed none is the failure worth avoiding.
    """
    found: list[dict[str, Any]] = []
    for raw in _MARKER.findall(text or ""):
        try:
            parsed = json.loads(raw)
        except ValueError:
            logger.warning("dropping an AI-RULING line that is not JSON")
            continue
        if not isinstance(parsed, dict):
            continue
        identity = str(parsed.get("identity") or "").strip()
        verdict = str(parsed.get("verdict") or "").strip()
        why = str(parsed.get("why") or "").strip()
        if not identity or verdict not in VERDICTS or not why:
            logger.warning("dropping an AI-RULING for %r: needs identity, a known verdict and a reason", identity)
            continue
        if any(row["identity"] == identity for row in found):
            continue  # one standing verdict per condition; the first wins
        found.append({"identity": identity, "verdict": verdict, "why": why[:_WHY_MAX]})

    kept = found[:_PER_RUN]
    if not kept:
        return text or "", []
    note = f"_({len(kept)} condition ruling{'' if len(kept) == 1 else 's'} filed with the judge.)_"
    replaced = False

    def _once(_match: re.Match[str]) -> str:
        nonlocal replaced
        if replaced:
            return ""
        replaced = True
        return note

    stripped = _MARKER.sub(_once, text or "")
    return stripped.rstrip() + ("\n" if text and text.endswith("\n") else ""), kept


def payloads(rulings: list[dict[str, Any]], *, model: str, secret: str) -> list[tuple[bytes, dict[str, str]]]:
    """Signed bodies, ready to post. Separated from the sending so the signing is
    testable without a socket — the sender is one `httpx.post` per row."""
    out: list[tuple[bytes, dict[str, str]]] = []
    for row in rulings:
        body = json.dumps({**row, "model": model}, ensure_ascii=False, sort_keys=True).encode()
        out.append((body, sign_timestamped(secret, body)))
    return out


# ── the local ledger, and the gate that reads it ─────────────────────────────
#
# The judge holds the authoritative copy of every filed ruling; this service
# keeps its own append-only line per ruling it FILED, because acting on a
# standing verdict must not require read access to a sibling's ledger — the
# probe consulting the judge for permission to skip work would be a new
# credential and a new failure mode, for a fact this process produced itself.

_LOCAL = "rulings.jsonl"


def condition_of(identity: str) -> str:
    """The condition title inside a ruling identity.

    Identities arrive as `source|title|k=v` or `source|title` — built by the
    patrol from the judge's board. The middle segment is the alert title, which
    is the one key both doors of this service also know.
    """
    parts = identity.split("|")
    return (parts[1] if len(parts) >= 2 else parts[0]).strip()


def record_local(workdir: Path, filed: list[dict[str, Any]], *, model: str) -> None:
    """Append what was filed, before it is sent. Local first, so a judge outage
    (or an unconfigured one) does not also cost this service its own memory of
    the verdicts it produced."""
    path = Path(workdir) / _LOCAL
    with path.open("a", encoding="utf-8") as handle:
        for row in filed:
            handle.write(json.dumps({**row, "model": model, "at": time.time()}, ensure_ascii=False) + "\n")


def standing(workdir: Path, title: str, *, ttl_days: int) -> dict[str, Any] | None:
    """The freshest ruling for this condition, if it is still current.

    Latest wins across the whole file — a condition ruled not_worth_it in June
    and worth_it in July is worth_it. Older than the TTL returns None: the
    weekly patrol refiles every ruling it can still defend, so a verdict
    nothing has refiled is a verdict whose evidence nobody has looked at
    lately, and the gate must not keep citing it.
    """
    if ttl_days <= 0 or not title.strip():
        return None
    path = Path(workdir) / _LOCAL
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    wanted = title.strip()
    newest: dict[str, Any] | None = None
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or condition_of(str(row.get("identity") or "")) != wanted:
            continue
        if str(row.get("verdict") or "") not in VERDICTS:
            continue
        if newest is None or float(row.get("at") or 0) >= float(newest.get("at") or 0):
            newest = row
    if newest is None or (time.time() - float(newest.get("at") or 0)) > ttl_days * 86400:
        return None
    return newest
