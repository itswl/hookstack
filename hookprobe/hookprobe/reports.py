"""The report-shaped JSON for the cases where there is no investigation.

An OpenClaw-dialect caller renders one shape. A run that dies — exception, wall
clock, empty output — still has to hand it that shape, or the failure arrives as
an empty card and the caller waits out its own timeout instead of seeing the
error on its next poll. So a failure is itself a *report*: the same fields,
confidence 0.0, and a root_cause that says the runner failed rather than
pretending anything was diagnosed.

The budget refusal is that idea for the opposite reason. Nothing failed there —
the breaker did its job — but a silent drop is indistinguishable from a broken
pipe at the far end, so the refusal travels the family loop as a report whose
summary an operator can read: what the window has spent, what the ceiling is,
and which knob raises it.

The superseded refusal is the same move again, for a press instead of an alert:
the freshness cursor retired a procedure the condition had outrun, and the card
that carried the button had already told its operator the press landed.

These live outside the service because they are text, not orchestration. Every
string here is read by whoever is looking at a channel card, and the module that
schedules turns is not where their wording should be maintained.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

# Anchored like suggestions._MARKER, and for the same reason: reading structure
# out of a model's prose is done with one explicit line the prompt asks for, not
# by interpreting the paragraphs around it.
_VERDICT = re.compile(r"^\s*VERDICT:\s*([A-Za-z0-9_-]{1,32})\s*$", re.MULTILINE)


def verdict(text: str, allowed: Iterable[str]) -> str:
    """The report's own conclusion, admitted ONLY if the operator declared it.

    This value can reach a routing key, which means it can decide where money is
    spent — and this is the component that reads attacker-influenced text. So the
    vocabulary is closed: `allowed` comes from the deployment's env, and anything
    outside it is `""`. An injection can then at worst pick a wrong lane among
    lanes somebody already wrote down; it cannot invent a destination.

    Empty `allowed` (the default) means the feature is off and this always
    returns `""` — a deployment does not acquire a new routing input by
    upgrading. The LAST marker wins: a run that revises itself should end on its
    conclusion, and its first guess should not outrank it.

    Unknown labels return `""` rather than raising. The run already cost money;
    trading a delivered report for a typo in a label is the wrong exchange, and
    the empty value routes to whatever the config does with no verdict.
    """
    vocabulary = {str(item).strip().lower() for item in allowed if str(item).strip()}
    if not vocabulary:
        return ""
    found = _VERDICT.findall(text or "")
    for candidate in reversed(found):
        if candidate.strip().lower() in vocabulary:
            return candidate.strip().lower()
    return ""


# What a card carries before it becomes a wall somebody scrolls past. It was
# 800 — a paragraph — and the field it caps turned out not to hold paragraphs.
# A real investigation came back 2,296 characters and the card delivered 787 of
# them, cut mid-word, with nothing saying so: the operator read a confirmed
# finding and a list of gaps, and never saw that the recommended order of work
# was in the part that did not arrive.
_SUMMARY_MAX = 2400


def _clipped(text: str) -> str:
    """`text`, or as much of it as a card should carry, admitting when it cuts.

    Two things the old cap did not do. It cuts on a line boundary, so a card
    ends on a thought rather than half a word. And it says how much is missing,
    because a card showing part of an answer without admitting it is worse than
    one showing less: the full text is on the run either way, and only one of
    these tells the reader there is a reason to go and read it.
    """
    body = text.strip()
    if len(body) <= _SUMMARY_MAX:
        return body
    head = body[:_SUMMARY_MAX]
    # Only honour a boundary in the second half; a report whose first line runs
    # past the cap would otherwise be clipped to almost nothing.
    boundary = head.rfind("\n")
    if boundary > _SUMMARY_MAX // 2:
        head = head[:boundary]
    head = head.rstrip()
    return f"{head}\n\n… {len(body) - len(head)} more characters — the full report is on the run below."


def folded(turns: list[dict[str, Any]]) -> int:
    """How many times this session's context was folded away by the runtime."""
    return sum(len(turn.get("compactions") or []) for turn in turns or [])


def with_fold_note(summary: str, folds: int) -> str:
    """`summary`, admitting that the context behind it was folded, if it was.

    A compaction is the runtime deciding, mid-conversation, which of its own
    history to keep — and the engine's hook says why that matters afterwards:
    "a report with a gap in the middle of a long investigation has no other
    explanation available". The fact was recorded from the day the hook was
    written and shown only on the console's turn line, which is the surface
    nobody is looking at while they read the answer in a chat window.

    It is deliberately not a warning about correctness. A folded conversation is
    usually fine; what a reader loses is the ability to assume the answer saw
    everything, and that assumption is the one worth taking away.
    """
    if folds <= 0:
        return summary
    times = "once" if folds == 1 else f"{folds} times"
    return (
        f"{summary}\n\n_Context folded {times} during this conversation: the runtime summarised its own "
        "earlier turns to stay inside the window, so detail from before the fold may not be behind this answer._"
    )


def _gap_words(seconds: float) -> str:
    """How long ago, in the coarsest unit that is still true."""
    if seconds < 90:
        return "moments"
    if seconds < 5400:
        return f"{round(seconds / 60)} minutes"
    return f"{seconds / 3600:.1f} hours"


def with_recovery_note(summary: str, recovered_at: Any, finished_at: Any) -> str:
    """`summary`, admitting that the condition ended — if it did, and when.

    The one shape of stale card this system can actually produce. An
    investigation takes a minute; a recovery arriving during that minute is
    recorded on the run by `service.record_recovery` (which annotates and
    spends nothing, so it never shows up as a new turn) and then the report is
    delivered on schedule, recommending work for a condition that is over. The
    reader has no way to know: the card is identical either way.

    Deliberately an ADMISSION and not a suppression. The findings may still be
    worth reading — a flapping alert clears on its own and will be back — and a
    procedure proposed here may still be the right thing to run. What the reader
    loses without this line is the fact that it was written about a moment that
    has passed, which is the one thing they cannot see for themselves.
    """
    try:
        ended = float(recovered_at or 0.0)
    except (TypeError, ValueError):
        return summary
    if ended <= 0:
        return summary
    try:
        done = float(finished_at or 0.0)
    except (TypeError, ValueError):
        done = 0.0
    if not done:
        return f"{summary}\n\n_The condition this investigated has since ended._"
    if ended > done:
        gap = _gap_words(ended - done)
        return f"{summary}\n\n_The condition this investigated ended {gap} after this report was finished._"
    gap = _gap_words(done - ended)
    return (
        f"{summary}\n\n_The condition this investigated ended {gap} before this report was finished — "
        "anything proposed below was chosen while it was still firing._"
    )


def report_summary(text: str) -> str:
    """The one paragraph a channel card shows; the full text stays on the run."""
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict) and parsed.get("summary"):
            return _clipped(str(parsed["summary"]))
    except (TypeError, ValueError):
        pass
    return _clipped(text)


# Enough of a partial answer to be worth reading, capped so a failure card does
# not become a wall.
_PARTIAL_MAX = 4000


def failure_report(reason: str, produced: str = "") -> str:
    """A report-shaped JSON so an OpenClaw-dialect caller renders the failure.

    `produced` is whatever the engine returned before it was flagged. It used to
    be discarded: a patrol run on 2026-09-03 produced 1002 characters of
    analysis, was flagged with a subtype this module does not map, and had every
    word of it replaced by this template — including the line "No analysis was
    produced for this alert", which was false about work that cost $1.68. What
    the run managed to say now survives the failure that interrupted it, and the
    template stops asserting the opposite.
    """
    partial = " ".join(str(produced or "").split())[:_PARTIAL_MAX]
    root = f"The analysis runner failed before reaching a conclusion: {reason}"
    if partial:
        root += f" It had produced this much before the failure: {partial}"
    return json.dumps(
        {
            "summary": (
                f"hookprobe run failed after producing a partial answer: {reason}"
                if partial
                else f"hookprobe run failed: {reason}"
            ),
            "root_cause": {"status": "unknown", "description": root},
            "evidence": [],
            "impact": {
                "scope": "analysis pipeline",
                "severity": "unknown",
                "description": (
                    "A partial answer survives in root_cause."
                    if partial
                    else "No analysis was produced for this alert."
                ),
            },
            "timeline": [],
            "recommendations": [
                {
                    "priority": "P1",
                    "action": "Retry the analysis from the caller's side",
                    "reason": "The failure was in the runner, not necessarily in the alert itself.",
                }
            ],
            "unknowns": ["The investigation did not run to completion."],
            "assumptions": [],
            "next_checks": [],
            "confidence": 0.0,
        },
        ensure_ascii=False,
    )


def unanswered_report(reason: str) -> str:
    """A report-shaped answer for a question this service declined to spend on.

    The follow-up door refuses four ways and every one of them was silent. Its
    own docstring says a refusal is "a 200 with a reason: the pipe records it" —
    and the pipe records it in a ledger, because a 2xx from this service is a
    delivered delivery. Nothing reaches the person who typed. They watch the bot
    stop answering and cannot tell being declined from being broken.

    The reason travels; the remedy travels with it. A conversation that has hit
    its ceiling is not a failure, it is a conversation that has to start again
    somewhere — and saying which is the difference between a wall and a door.
    """
    summary = (
        f"This reply was not answered: {reason}. Nothing was spent on it, and the "
        "investigation above still stands. Raise a new alert or open a new question if "
        "this needs more work — a fresh investigation reads the case files this one left behind."
    )
    return json.dumps(
        {
            "summary": summary,
            "root_cause": {
                "status": "not_answered",
                "description": f"A chat reply reached this investigation and was declined: {reason}.",
            },
            "evidence": [],
            "impact": {
                "scope": "this conversation",
                "severity": "none",
                "description": "No turn was taken and no tool ran. The delivered report is unchanged.",
            },
            "timeline": [],
            "recommendations": [
                {
                    "priority": "P3",
                    "action": "Start a fresh investigation if the question still matters",
                    "reason": "A new session reads the case files this one wrote, without carrying its context.",
                }
            ],
            "unknowns": ["Whether the question still needs answering."],
            "assumptions": [],
            "next_checks": [],
            "confidence": 0.0,
        },
        ensure_ascii=False,
    )


def superseded_report(reason: str, steps: list[dict[str, Any]]) -> str:
    """A report-shaped refusal for a press that arrived after the world moved.

    It exists because the honest answer had nowhere to go. A press is forwarded
    by the bridge, enqueued by the pipe and answered "accepted and passed on"
    before this service has looked at it — and the card's buttons are stripped
    on the way, because their token is single-use. So a refusal decided here
    reaches the operator through NO existing path: the card says it landed, the
    procedure never ran, and the only trace is a line in a log.

    The budget breaker had the same problem and the same answer, which is why
    this is shaped like `budget_report`: refuse, then say so as a report and let
    the family loop carry it into the chat the press came from.
    """
    commands = [str(step.get("command") or "") for step in steps[:3] if step.get("command")]
    summary = (
        f"The approved procedure did NOT run: {reason}. It is retired rather than queued — "
        "these commands were chosen from evidence this condition has since moved past, and "
        "running them now would act on a system nobody has looked at since. "
        "Ask this investigation for a fresh look if it still needs one."
    )
    return json.dumps(
        {
            "summary": summary,
            "root_cause": {
                "status": "not_executed",
                "description": f"A remediation proposal was approved after {reason}.",
            },
            "evidence": [{"what": "the steps that did not run", "detail": command} for command in commands],
            "impact": {
                "scope": "remediation",
                "severity": "none",
                "description": "Nothing was executed against the target. No step ran and none is pending.",
            },
            "timeline": [],
            "recommendations": [
                {
                    "priority": "P2",
                    "action": "Ask the investigation for a fresh look, and approve what it proposes then",
                    "reason": "A procedure is a decision about a moment; this one outlived its moment.",
                }
            ],
            "unknowns": ["Whether the condition still needs any of these steps."],
            "assumptions": [],
            "next_checks": [],
            "confidence": 0.0,
        },
        ensure_ascii=False,
    )


def cooling_report(reason: str, steps: list[dict[str, Any]]) -> str:
    """A report-shaped refusal for a press whose target was just acted on.

    Sibling of `superseded_report`, and the difference between them is the whole
    point of having two: that one retires a procedure, this one defers it. The
    recommendation is therefore "press again after the window", not "ask for a
    fresh look" — nothing about these steps has been invalidated, they are
    simply not the second thing to do to a machine inside a quarter of an hour.
    """
    commands = [str(step.get("command") or "") for step in steps[:3] if step.get("command")]
    summary = (
        f"The approved procedure did NOT run: {reason}. It is held, not retired — the steps "
        "are still valid and the proposal is still approvable. Two changes to one target "
        "inside the cooldown is the shape of a flap, so the second one waits for somebody "
        "to see what the first one did."
    )
    return json.dumps(
        {
            "summary": summary,
            "root_cause": {
                "status": "not_executed",
                "description": f"A remediation approval was held back: {reason}.",
            },
            "evidence": [{"what": "the steps that did not run", "detail": command} for command in commands],
            "impact": {
                "scope": "remediation",
                "severity": "none",
                "description": "Nothing was executed against the target. No step ran and none is pending.",
            },
            "timeline": [],
            "recommendations": [
                {
                    "priority": "P2",
                    "action": "Check what the earlier procedure changed, then approve this one again if still needed",
                    "reason": "The cooldown buys a look at the first change; it does not decide about the second.",
                }
            ],
            "unknowns": ["Whether the earlier procedure already fixed what this one is for."],
            "assumptions": [],
            "next_checks": [],
            "confidence": 0.0,
        },
        ensure_ascii=False,
    )


def budget_report(spent: float, budget: float, window_hours: float) -> str:
    """A report-shaped refusal, so the family loop completes without an engine run."""
    summary = (
        f"Budget breaker open: investigations have spent ${spent:.2f} in the last "
        f"{window_hours:g}h (budget ${budget:.2f}), so this alert was NOT investigated. "
        "The judge's verdict is unaffected. Investigations resume when the window slides "
        "or HOOKPROBE_BUDGET_USD is raised."
    )
    return json.dumps(
        {
            "summary": summary,
            "root_cause": {
                "status": "not_investigated",
                "description": "The investigation budget for the current window is exhausted; "
                "the run was refused before the engine started.",
            },
            "evidence": [],
            "impact": {
                "scope": "analysis pipeline",
                "severity": "none",
                "description": "Only the deep investigation was skipped; the alert and its verdict are unaffected.",
            },
            "timeline": [],
            "recommendations": [
                {
                    "priority": "P2",
                    "action": "Raise HOOKPROBE_BUDGET_USD or wait for the window to slide, "
                    "then re-send the event if the alert still matters",
                    "reason": "The breaker refuses new autonomous investigations; it does not queue them.",
                }
            ],
            "unknowns": ["No investigation was run for this alert."],
            "assumptions": [],
            "next_checks": [],
            "confidence": 0.0,
        },
        ensure_ascii=False,
    )
