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

These live outside the service because they are text, not orchestration. Every
string here is read by whoever is looking at a channel card, and the module that
schedules turns is not where their wording should be maintained.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable

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
