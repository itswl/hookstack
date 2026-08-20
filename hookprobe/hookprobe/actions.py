"""What a card may ask of a report, and the ledger that keeps a redelivery free.

The investigation reaches a person as a Feishu/DingTalk/WeCom card, and that card
used to be a dead end. Everything a follow-up needs already existed on this side
— a session that can be resumed with its whole evidence trail in context, a
proposal parked behind its two gates — but reaching any of it meant leaving the
chat, finding the console URL and presenting a bearer token. At 3am, on a phone,
that is the same as not having it.

The split with the pipe is the family's usual one: this side DECLARES which
actions its report deserves, because that is a judgement about the
investigation, and the pipe mints the signed card token and owns the IM
callback, because a card token is channel edge. So nothing here names a channel
and nothing here signs a button. Declaring is a request, too: the pipe drops
kinds it is not configured to accept.

Declaring and dispatching are two halves of one vocabulary, which is why they
live in one module. A kind a report offers and the door does not know is a
button that fails in somebody's hand, and the failure surfaces in a chat window
rather than in a test.

The ledger is here for a blunter reason: `followup` starts a paid model turn and
`approve` runs commands against a live target, while an IM platform retries any
callback it did not hear an answer to. Both of those have to happen once per
press, not once per delivery. So a claim file is created with O_EXCL — the
create either wins or raises, with no window between looking and acting — and
the second delivery of one press reads back the first one's answer instead of
buying a second investigation. The marker is what makes this idempotent; the
answer recorded beside it is only so the pipe has something to show.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from hookprobe import remediation, suggestions
from hookprobe.files import atomic_write
from hookprobe.runs import Run

logger = logging.getLogger("hookprobe.actions")

DIRNAME = "actions"

# The whole vocabulary. `kind` is what the door dispatches on and what the pipe
# filters by; everything else on a declared action rides along as an opaque
# param, so adding a question needs no new kind.
KINDS = ("followup", "approve", "useful", "useless", "remember")
# The two that are a human's ruling on the report rather than a request of it.
RULINGS = ("useful", "useless")

# A card button is read in one glance, so the text has to fit in one. Feishu
# will render a longer label; a person will not read it.
_TEXT_MAX = 72
# One proposal per report is the normal case (a follow-up turn that proposes
# again parks its own). More than a few approve buttons is not a card, it is a
# form, and a form is what the console is for.
_MAX_APPROVE = 3
# Lower than the per-run suggestion cap on purpose: a card with four buttons
# asking a person to adopt four permanent instructions is not a card.
_MAX_REMEMBER = 2
_KEY_MAX = 400

_WHY_PROMPT = (
    "Why do you believe that? Name the specific evidence behind your conclusion — which command "
    "or file, and what it showed — and say what would change your mind."
)
_RESUME_PROMPT = (
    "This investigation did not finish. Say what you had established before it stopped, what is "
    "still unknown, and the single next step you would take."
)


def followup_prompt(run: Run) -> str:
    """The question a follow-up asks when nobody supplied one.

    Two defaults, because a delivered conclusion and an investigation that died
    leave different holes: the first has a claim that can be pressed on, the
    second has only whatever it managed to establish before it stopped. Asking
    a crashed run "why do you believe that" gets an apology, not evidence.
    """
    return _RESUME_PROMPT if run.error else _WHY_PROMPT


def declare(run: Run, workdir: Path) -> list[dict[str, Any]]:
    """Which actions this report deserves — the judgement, not the buttons.

    Three groups, and the reasoning differs for each:

    * `followup` only when the run left an engine session behind. A resumable
      session is what makes the follow-up cheap (it keeps every tool result the
      first pass gathered); without one there is nothing to continue, and a
      button that cannot work is worse than an absent one.
    * `approve`, one per proposal still sitting at `proposed`, and only ever
      then. A report that proposed nothing has nothing to approve, and a
      proposal already approved or rejected from the console must not offer a
      second press. The text names the COMMAND, because that is what the shell
      gets — see `_approve_text`.
    * the ruling pair, on every report including the failures. Their whole
      purpose is a number that answers "should I pay a model per alert", and a
      count that quietly excluded the disappointing runs would not answer it.
    """
    declared: list[dict[str, Any]] = []
    if run.engine_session_id:
        declared.append(
            {
                "kind": "followup",
                "text": "Ask why" if not run.error else "Ask what you found",
                "prompt": followup_prompt(run),
            }
        )
    for row in _open_proposals(run.session_key, workdir):
        declared.append(
            {"kind": "approve", "text": _approve_text(row.get("steps") or []), "ref": str(row.get("id") or "")}
        )
    # One per memory line this run proposed that is still waiting. Only this
    # run's, and only while open — the same rule as `approve`, for the same
    # reason: a button that cannot work is worse than an absent one.
    #
    # The queue used to be the ONLY path for these and nothing ever came down
    # it. Most lines now apply themselves (hookprobe.suggestions), so what
    # reaches this button is the residue the shape check refused — which is
    # exactly the set worth one tap from a person rather than a login.
    for row in _open_suggestions(run.session_key, workdir):
        declared.append(
            {
                "kind": "remember",
                # The LINE, not "accept a suggestion". It is about to become
                # standing instruction for every later run; a button that does
                # not say what it will write is the same trap as one that does
                # not say what it will run.
                "text": f"Remember: {str(row.get('line') or '')[:80]}",
                "ref": str(row.get("id") or ""),
            }
        )
    declared.append({"kind": "useful", "text": "Found the cause"})
    declared.append({"kind": "useless", "text": "Missed it"})
    return declared


def _open_proposals(session_key: str, workdir: Path) -> list[dict[str, Any]]:
    """This run's procedures that are still waiting on somebody, newest first."""
    rows = [
        row
        for row in remediation.list_all(workdir, limit=200)
        if str(row.get("session_key") or "") == session_key and row.get("status") == "proposed"
    ]
    return rows[:_MAX_APPROVE]


def _open_suggestions(session_key: str, workdir: Path) -> list[dict[str, Any]]:
    """This run's proposed memory lines that are still waiting, newest first."""
    rows = [
        row
        for row in suggestions.load(workdir)
        if str(row.get("session_key") or "") == session_key and row.get("status") == "open"
    ]
    return rows[-_MAX_REMEMBER:]


def _approve_text(steps: list[dict[str, Any]]) -> str:
    """A button that does not say what will run is a trap.

    The command, not the step's `action` prose: `action` is the model's
    description of its intent and `command` is what reaches the shell, and
    those two are exactly what must not be confused at the moment somebody
    presses a button. The risk rides along for the same reason — the console
    shows every step and its risk before it asks for a confirmation, and a card
    has one line to do the same job with.
    """
    commands = [text for text in (str(step.get("command") or "").strip() for step in steps) if text]
    if not commands:
        return "Approve the proposed procedure"
    risks = {str(step.get("risk") or "").lower() for step in steps}
    risk = "high" if "high" in risks else "medium" if "medium" in risks else ""
    label = f"Approve ({risk} risk)" if risk else "Approve"
    rest = len(commands) - 1
    body = commands[0] if not rest else f"{commands[0]} +{rest} more"
    return _clip(f"{label}: {body}", _TEXT_MAX)


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def key(correlation_id: str, kind: str, at: Any) -> str:
    """The identity of one press: (correlation_id, kind, at).

    Not the correlation id alone — one card legitimately carries several
    buttons, and pressing "Ask why" must not consume the approval. Not without
    `at` either: a person who presses the same button twice an hour apart means
    it twice, and only the timestamp tells that apart from the platform saying
    the same thing twice.

    A delivery that carries no `at` collapses all of its presses into one key,
    which is the safe direction to fail in: with nothing to tell a retry from a
    second press, the door declines to spend rather than guessing.
    """
    return f"{correlation_id}\x1f{kind}\x1f{at}"[:_KEY_MAX]


def claim(workdir: Path, ledger_key: str) -> dict[str, Any] | None:
    """Take this delivery, or hand back the row the first delivery left.

    None means the claim is fresh and the caller owns the work. Anything else
    means this press has already been seen, and the caller must not act again —
    if the row carries an `answer`, that is what the first delivery did; if it
    does not, the first delivery is still in flight.

    Raises OSError when the claim cannot be written, and the door turns that
    into a refusal rather than proceeding: this door starts paid turns and runs
    commands, so spending without being able to account for it once is not a
    degraded mode worth having.
    """
    directory = workdir / DIRNAME
    directory.mkdir(parents=True, exist_ok=True)
    path = _path(directory, ledger_key)
    try:
        handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        # A row that will not parse is treated as in flight, which is the safe
        # direction to guess in: it costs a press, never a second spend.
        return _read(path) or {"key": ledger_key, "answer": None}
    try:
        with os.fdopen(handle, "wb") as file:
            file.write(_encode({"key": ledger_key, "claimed_at": round(time.time(), 3), "answer": None}))
    except OSError:
        path.unlink(missing_ok=True)
        raise
    return None


def settle(workdir: Path, ledger_key: str, answer: dict[str, Any]) -> None:
    """Record what this press did, so a redelivery is answered instead of repeated.

    Best-effort on purpose. The claim marker above is what makes the door
    idempotent; this only decides whether a redelivery hears "here is what
    happened" or "somebody is already on it", and neither of those spends
    anything. Losing it is worth a line in the log and nothing more — the work
    it describes has already happened, and failing the response would make the
    pipe retry a press that already landed.
    """
    path = _path(workdir / DIRNAME, ledger_key)
    row = _read(path) or {"key": ledger_key, "claimed_at": round(time.time(), 3)}
    row["answer"] = answer
    row["settled_at"] = round(time.time(), 3)
    try:
        atomic_write(path, _encode(row))
    except OSError:
        logger.warning("could not record what a card action did key=%s", ledger_key[:80], exc_info=True)


def release(workdir: Path, ledger_key: str) -> None:
    """Give back a claim the dispatch never used.

    A delivery that named a session or a proposal which does not exist did no
    work, so it must not hold the key: otherwise the redelivery that arrives
    after somebody fixes the target is answered "already in flight" by a claim
    with nothing behind it, and the press is lost for good.
    """
    with contextlib.suppress(OSError):
        _path(workdir / DIRNAME, ledger_key).unlink(missing_ok=True)


def _path(directory: Path, ledger_key: str) -> Path:
    """One file per press, named by digest — the key itself is the pipe's text."""
    return directory / f"{hashlib.sha256(ledger_key.encode('utf-8')).hexdigest()[:24]}.json"


def _encode(row: dict[str, Any]) -> bytes:
    return (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")


def _read(path: Path) -> dict[str, Any] | None:
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return row if isinstance(row, dict) else None
