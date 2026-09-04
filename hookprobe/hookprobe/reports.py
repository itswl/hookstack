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


def report_summary(text: str) -> str:
    """The one paragraph a channel card shows; the full text stays on the run."""
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict) and parsed.get("summary"):
            return str(parsed["summary"])[:800]
    except (TypeError, ValueError):
        pass
    return text.strip()[:800]


def failure_report(reason: str) -> str:
    """A minimal report-shaped JSON so an OpenClaw-dialect caller renders the failure."""
    return json.dumps(
        {
            "summary": f"hookprobe run failed: {reason}",
            "root_cause": {
                "status": "unknown",
                "description": f"The analysis runner failed before reaching a conclusion: {reason}",
            },
            "evidence": [],
            "impact": {
                "scope": "analysis pipeline",
                "severity": "unknown",
                "description": "No analysis was produced for this alert.",
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
