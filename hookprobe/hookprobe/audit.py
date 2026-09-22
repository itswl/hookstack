"""The flight recorder: one line per tool call, and the chain that binds them.

The record and the DECISION are different jobs, and this module exists because
they had drifted into one file. `gate.py` says what a call may do; this says
what happened, keeps it where the agent cannot edit it, and answers the three
questions somebody asks of it afterwards — how often was this turn refused, did
any tool output carry a credential, and was the gate consulted at all.

Its own invariant, and the reason the write is shaped the way it is: **a line is
never lost to keep the chain**. If the link cannot be maintained the line is
written unchained and `verify_chain` reports the gap. A missing audit line is
worse than an unverifiable one — the second can be reported, the first leaves
nothing to report.

Cheap to import, for the same reason gate.py is: two of the three runtimes spawn
`python -m hookprobe.gate` per tool call, so this is imported on that path.
Standard library only.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

# The audit's chain. Each line carries the hash of the one before it, so an
# edit or a deletion after the fact stops the chain and can be pointed at.
#
# This is the claim a compliance reader most wants and the one the node could
# not make: the flight recorder was append-only JSONL, and nothing detected a
# line rewritten later. It is deliberately NOT a distributed ledger — the
# threat is somebody quietly tidying a record on this disk, and a hash chain
# plus an off-box copy answers that, where a blockchain answers a question
# nobody here is asking.
_CHAIN_SEED = "hookstack/audit/1"
_CHAIN_FILE = ".chain"
# How far back a verification walks by default. The whole history is the honest
# answer and an unbounded read on a per-call path is not; the caller can ask
# for more.
CHAIN_DAYS = 7


def _line_hash(line: dict[str, Any]) -> str:
    """A line's digest, over everything but the digest itself.

    Canonical JSON rather than the bytes on disk, so a reader that re-serialises
    differently still verifies — the record is the FACTS, not the formatting.
    """
    body = {key: value for key, value in line.items() if key != "hash"}
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def append(audit_dir: Path, line: dict[str, Any]) -> None:
    """One JSONL line into today's audit file, linked to the one before it.

    Never raises, and never loses the line: if the chain cannot be maintained —
    a locked file, a read-only mount, anything — the line is still written,
    unchained. A missing audit line is worse than an unverifiable one, and a
    verification that reports the gap is better than a writer that dropped it.
    """
    audit_dir.mkdir(parents=True, exist_ok=True)
    day_file = audit_dir / (time.strftime("%Y-%m-%d") + ".jsonl")
    try:
        import fcntl  # noqa: PLC0415 — linux/macOS only, and only on this path

        # The lock is on the chain file, not the day file, because the day file
        # rolls over at midnight and the chain does not. Held across the whole
        # read-modify-write: the gate is SPAWNED per tool call, so two writers
        # racing on the same `prev` is the ordinary case, not the rare one.
        chain = audit_dir / _CHAIN_FILE
        with chain.open("a+", encoding="utf-8") as state:
            fcntl.flock(state.fileno(), fcntl.LOCK_EX)
            state.seek(0)
            previous = (state.read() or "").strip() or _CHAIN_SEED
            linked = {**line, "prev": previous}
            linked["hash"] = _line_hash(linked)
            with day_file.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(linked, ensure_ascii=False) + "\n")
            state.seek(0)
            state.truncate()
            state.write(linked["hash"])
    except Exception:  # noqa: BLE001 — see the docstring: the line matters more
        # Unchained rather than lost. `verify_chain` counts these and, once the
        # chain has started, treats a later one as a break — so the gap is
        # reported by the reader instead of being swallowed by the writer.
        with day_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(line, ensure_ascii=False) + "\n")


def chain_head(audit_dir: Path) -> str:
    """The hash of the newest linked line, or "" when nothing is chained yet.

    Cheap on purpose — one small file, no walk — because this is read on the
    path of every report that goes home, and a per-report walk of the audit
    would be a cost nobody agreed to.
    """
    try:
        return (audit_dir / _CHAIN_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def chain_anchored(audit_dir: Path, digest: str, days: int = CHAIN_DAYS) -> bool:
    """Does any line in this node's chain still hash to `digest`?

    This is the question an OFF-BOX copy asks. Each report that goes home
    carries the chain head as it stood when the report was written, and the
    pipe keeps that payload in its own ledger on its own disk. So a record
    rewritten here later fails twice: `verify_chain` stops adding up locally,
    and a head somebody else wrote down no longer names any line this node has.

    The second is the one that matters, because it survives an editor who can
    also rewrite `.chain` — rebuilding a self-consistent chain is easy, and
    rebuilding one that still contains a hash another service recorded hours
    ago is not.
    """
    if not digest or not audit_dir.is_dir():
        return False
    for path in sorted(audit_dir.glob("*.jsonl"))[-max(1, days) :]:
        try:
            for raw in path.read_text(encoding="utf-8").splitlines():
                if digest in raw:
                    try:
                        if json.loads(raw).get("hash") == digest:
                            return True
                    except ValueError:
                        continue
        except OSError:
            continue
    return False


def verify_chain(audit_dir: Path, days: int = CHAIN_DAYS) -> dict[str, Any]:
    """Walk the audit in order and report where, if anywhere, it stops adding up.

    Three outcomes worth telling apart, and the third is why this returns a
    record rather than a bool:

      * `intact` — every chained line's digest recomputes and names its
        predecessor.
      * `broken_at` — a line was edited or one was removed. The FIRST such line
        is named; everything after it is unverifiable rather than wrong.
      * `unchained` — lines written before this existed, or by a writer whose
        lock failed. Counted, never treated as a break: an honest gap is not
        evidence of tampering, and conflating them would make the alarm useless
        on the first day it ran.
    """
    if not audit_dir.is_dir():
        return {"intact": True, "checked": 0, "unchained": 0, "broken_at": None, "files": 0}
    files = sorted(audit_dir.glob("*.jsonl"))[-max(1, days) :]
    previous, checked, unchained = _CHAIN_SEED, 0, 0
    started = False
    for path in files:
        try:
            raw_lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            return {"intact": False, "checked": checked, "unchained": unchained, "broken_at": f"{path.name}: {exc}"}
        for number, raw in enumerate(raw_lines, start=1):
            if not raw.strip():
                continue
            try:
                line = json.loads(raw)
            except ValueError:
                return {
                    "intact": False,
                    "checked": checked,
                    "unchained": unchained,
                    "broken_at": f"{path.name}:{number} is not JSON",
                }
            if "hash" not in line:
                unchained += 1
                # Before the chain existed is a gap; after it started is a break.
                if started:
                    return {
                        "intact": False,
                        "checked": checked,
                        "unchained": unchained,
                        "broken_at": f"{path.name}:{number} has no link",
                    }
                continue
            started = True
            if _line_hash(line) != line["hash"]:
                return {
                    "intact": False,
                    "checked": checked,
                    "unchained": unchained,
                    "broken_at": f"{path.name}:{number}",
                }
            if not checked and str(line.get("prev") or "") != previous:
                # 窗口的第一条链行指向窗外——创世滚出 CHAIN_DAYS 之后这恒为真，
                # 2026-09-17 起每次校验都在这里"断"，而链根本没断。窗口内的链从这
                # 一行的自身 hash 起步：它指向哪里是窗外的事，锚定靠 off-box 的
                # meta.audit_head，不靠这里的种子。
                previous = str(line.get("prev") or previous)
            elif str(line.get("prev") or "") != previous:
                return {
                    "intact": False,
                    "checked": checked,
                    "unchained": unchained,
                    "broken_at": f"{path.name}:{number}",
                }
            previous, checked = str(line["hash"]), checked + 1
    return {"intact": True, "checked": checked, "unchained": unchained, "broken_at": None, "files": len(files)}


def trips(audit_dir: Path, session_key: str, *, since: float) -> int:
    """How many times this turn was refused by the guard.

    Borrowed in idea from `ToolGuardrailFunctionOutput`, which has three
    outcomes where this stack had two: allow, and reject-with-a-reason. There
    was no third — nothing escalated, so a run that argued with the guard
    twenty times left exactly the same trace as one that was refused once and
    rephrased. That is the difference between an agent narrowing a query and an
    agent being steered, and it was invisible.

    Counted from the audit rather than kept in memory, because the gate is
    STATELESS on two of the three runtimes: codex spawns
    `python -m hookprobe.gate` per tool call and pi shells out to the same
    command, so a counter in a process would reset between every call. The
    record already exists and is already append-only; this reads it.
    """
    since -= 0.001
    day_file = audit_dir / (time.strftime("%Y-%m-%d") + ".jsonl")
    seen = 0
    try:
        with day_file.open(encoding="utf-8") as handle:
            for raw in handle:
                try:
                    line = json.loads(raw)
                except ValueError:
                    continue
                if (
                    line.get("session") == session_key
                    and line.get("denied") is True
                    and float(line.get("ts") or 0) >= since
                ):
                    seen += 1
    except OSError:
        return 0
    return seen


def output_secrets(audit_dir: Path, session_key: str) -> list[str]:
    """Which kinds of credential this session's tool output appeared to contain.

    The detector writes `output_secret` beside the call; this is the read. It
    exists because the flag was landing in a JSONL nobody opens — to find out
    that a run's context had been filled with a live key you had to SSH in and
    grep, which is exactly when you no longer need to know. Whole file, not a
    time window: contamination is a fact about the session, not about one turn.
    """
    day_glob = sorted(audit_dir.glob("*.jsonl")) if audit_dir.is_dir() else []
    found: list[str] = []
    for path in day_glob:
        try:
            for raw in path.read_text(encoding="utf-8").splitlines():
                try:
                    line = json.loads(raw)
                except ValueError:
                    continue
                kind = line.get("output_secret")
                if kind and line.get("session") == session_key and kind not in found:
                    found.append(str(kind))
        except OSError:
            continue
    return sorted(found)


def consulted(audit_dir: Path, session_key: str, *, since: float) -> bool:
    """Did the gate actually record anything for this session during this turn?

    `verify()` proves the gate ANSWERS. It does not prove the runtime ASKS, and
    those are different claims that look identical from inside this process.
    The difference was measured: driving codex through its app-server leaves a
    perfectly working gate command sitting unused, so `verify()` passes, the
    posture is reported, and `kubectl delete` runs. Nothing anywhere noticed.

    So a turn that ran tools and left no line here is a turn that ran ungated,
    and the caller treats that as a broken node rather than a quiet oddity.
    """
    # The recorder stamps `round(time.time(), 3)`, which can land a hair BELOW
    # the caller's unrounded start. Half a millisecond of slack, so a line
    # written the instant the turn began still counts as written during it.
    since -= 0.001
    day_file = audit_dir / (time.strftime("%Y-%m-%d") + ".jsonl")
    try:
        with day_file.open(encoding="utf-8") as handle:
            for raw in handle:
                try:
                    line = json.loads(raw)
                except ValueError:
                    continue
                if line.get("session") == session_key and float(line.get("ts") or 0) >= since:
                    return True
    except OSError:
        return False
    return False
