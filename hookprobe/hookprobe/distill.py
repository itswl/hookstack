"""Turn a finished investigation into a skill draft a human can approve.

The investigator is already told to read prior case files, and the skills
directory is described as what previous runs distilled — but nothing ever wrote
one, so the loop was open at exactly the step that would make the next
investigation cheaper.

It closes here as a **draft**, never a write. An agent that can silently edit
what it will be told next time is an agent whose future context nobody reviewed;
one bad conclusion would then teach itself forward. So this assembles the draft
and returns it, and the existing `PUT /v1/skills/{name}` — an operator action —
is still the only way anything lands on the volume.

Assembled from the record rather than by asking a model: the run already knows
what was asked, which tools ran in what order, and what it concluded. A second
model call would cost money to restate that, and would be free to invent.
"""

from __future__ import annotations

import json
import re
from typing import Any

_SLUG = re.compile(r"[^a-z0-9]+")
# What a step did, minus the noise of reading its own instructions.
_SKIP_TOOLS = ("TodoWrite",)


def slug(text: str, fallback: str = "investigation") -> str:
    cleaned = _SLUG.sub("-", text.lower()).strip("-")
    return (cleaned[:48].strip("-") or fallback).strip("-")


def _steps(turns: list[dict[str, Any]]) -> list[str]:
    """The tool sequence, in order, with consecutive repeats collapsed."""
    steps: list[str] = []
    for turn in turns:
        for event in turn.get("events") or []:
            if event.get("type") != "tool_use" or event.get("name") in _SKIP_TOOLS:
                continue
            line = f"{event.get('name', '?')} {str(event.get('detail') or '').strip()}".strip()
            if not steps or steps[-1] != line:
                steps.append(line)
    return steps


def _conclusion(turns: list[dict[str, Any]]) -> str:
    """The investigation's report — the first turn, not the last.

    The first turn is the answer to the alert; it is the one the family loop
    returns. Later turns are an operator exploring afterwards, and taking the
    last one put "which model are you?" into a runbook.
    """
    for turn in turns:
        text = str(turn.get("text") or "").strip()
        if not text:
            continue
        # A caller that asked for structured output gets an object back, and
        # dumping it verbatim put a whole JSON document under "what it turned
        # out to be". Prefer the fields written for a human to read.
        report = _as_object(text)
        if report is not None:
            for key in ("summary", "root_cause", "conclusion", "primary_text", "impact"):
                value = str(report.get(key) or "").strip()
                if value:
                    return value[:600]
        # The report's opening paragraph is written to be quotable on its own.
        return text.split("\n\n")[0][:600]
    return ""


def _as_object(text: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(text)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _headline(turns: list[dict[str, Any]], current_message: str) -> str:
    """What this investigation was about, not how it was instructed.

    An event-door run's first message is the caller's whole analysis prompt,
    whose first line is a role description ("you are the unattended SRE agent
    …"). Naming a runbook after that produces one called
    webhookwise-sre-agent-webhook-webhook. The alert is further down, on the
    Title: line the family loop writes, or named in the structured report.
    """
    message = str((turns[0] if turns else {}).get("message") or current_message or "")
    for line in message.splitlines():
        stripped = line.strip()
        for prefix in ("Title:", "标题:", "Rule:", "告警:"):
            if stripped.startswith(prefix):
                titled = stripped[len(prefix) :].strip()
                if titled:
                    return titled
    for turn in turns:
        report = _as_object(str(turn.get("text") or "").strip())
        if report:
            identity = report.get("alert_identity")
            if isinstance(identity, dict):
                named = str(identity.get("rule_name") or identity.get("service") or "").strip()
                if named:
                    return named
    first = next((line.strip() for line in message.splitlines() if line.strip()), "")
    return first


def draft_skill(run: Any, *, title: str = "") -> dict[str, str]:
    """A SKILL.md draft for the condition this run investigated."""
    turns = list(getattr(run, "turns", []) or [])
    headline = title or _headline(turns, str(getattr(run, "current_message", "") or "")) or run.session_key
    name = slug(headline)
    steps = _steps(turns)
    conclusion = _conclusion(turns)

    lines = [
        "---",
        f"name: {name}",
        f'description: What a previous investigation of "{headline[:90]}" checked, and what it found.',
        "---",
        "",
        f"# {headline[:120]}",
        "",
        f"Distilled from session `{run.session_key}`"
        + (f" (engine session `{run.engine_session_id}`)." if getattr(run, "engine_session_id", "") else "."),
        "",
        "## What was checked, in order",
        "",
    ]
    if steps:
        lines += [f"{index}. `{step}`" for index, step in enumerate(steps[:30], 1)]
    else:
        lines.append("_No tools ran — the answer came from the alert alone._")
    lines += ["", "## What it turned out to be", "", conclusion or "_(the run left no conclusion)_", ""]
    lines += [
        "## Before trusting this next time",
        "",
        "- Dead ends are **not** recorded: the run keeps which tools ran, not what",
        "  they returned, so a step listed above may have been a wasted one. Delete",
        "  the steps that did not help before saving.",
        "- Anything specific to that day — hostnames, amounts, a one-off outage —",
        "  belongs in the description or nowhere.",
        "",
    ]
    return {"name": name, "content": "\n".join(lines)}
