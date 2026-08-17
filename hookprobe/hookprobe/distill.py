"""Turn a finished investigation into a runbook the next one can use.

The investigator is already told to read prior case files, and the skills
directory is described as what previous runs distilled — but nothing ever wrote
one, so the loop was open at exactly the step that would make the next
investigation cheaper.

Two ways to close it, and the difference is *who writes*, not whether it is
automatic:

* `draft_skill` assembles a draft and returns it. `POST /v1/runs/{key}/distill`
  hands it to an operator, who saves it with `PUT /v1/skills/{name}`.
* `auto_install` does the same assembly and writes it, at the end of a run,
  **from the service** — which is the point. The agent's own Write and Edit
  cannot reach `.claude/` (see hookprobe.inputs): an agent that can edit its
  future instructions mid-run is one injected line away from teaching itself
  forward, and nothing in the record would say so. The service writing a
  runbook assembled from the run's own structure is a different act with a
  different failure mode, and it is the one that can be made safe.

What makes it safe is not review, since there is none — it is the shape of the
write:

* assembled from the record, never from free text the run chose: the question,
  the tool sequence, the conclusion;
* **create-only**. Replacing an existing runbook stays an operator action, so a
  bad run cannot overwrite a good one, and an injection cannot quietly rewrite
  what an operator approved;
* never from a run that failed, and never from a run that changed its own
  inputs — one that already misbehaved does not get to leave instructions;
* capped, because every runbook is prefix cost on every later run;
* stamped with where it came from, and marked unreviewed in its own text, so
  neither the next run nor the next operator mistakes it for doctrine.

Assembled from the record rather than by asking a model: the run already knows
what was asked, which tools ran in what order, and what it concluded. A second
model call would cost money to restate that, and would be free to invent.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
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
        for key in ("summary", "root_cause", "conclusion", "primary_text", "impact"):
            value = _field(text, key)
            if value:
                return value[:600]
        # The report's opening paragraph is written to be quotable on its own.
        return text.split("\n\n")[0][:600]
    return ""


# A quoted string value for a named key, wherever it sits in the document.
_FIELD = '"{key}"\\s*:\\s*"((?:[^"\\\\]|\\\\.)*)"'


def _field(text: str, key: str) -> str:
    """One field out of a report, parsed or not.

    Strict parsing is tried first and usually works. It must not be the only
    way in: a real production report came back 4,671 characters long, correctly
    terminated, and invalid at line 20 — the model had emitted one malformed
    entry. Everything else in it was still readable, and a runbook draft that
    degrades to quoting a brace because of one bad line is worse than one that
    reads the field it wanted.
    """
    try:
        parsed = json.loads(text)
    except ValueError:
        parsed = None
    if isinstance(parsed, dict):
        found = _walk(parsed, key)
        if found:
            return found
    match = re.search(_FIELD.format(key=re.escape(key)), text)
    if not match:
        return ""
    try:
        return str(json.loads(f'"{match.group(1)}"')).strip()
    except ValueError:
        return match.group(1).strip()


def _walk(obj: dict[str, Any], key: str) -> str:
    value = obj.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    for nested in obj.values():
        if isinstance(nested, dict):
            found = _walk(nested, key)
            if found:
                return found
    return ""


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
        text = str(turn.get("text") or "").strip()
        for key in ("rule_name", "alert_name", "service"):
            named = _field(text, key)
            if named:
                return named
    first = next((line.strip() for line in message.splitlines() if line.strip()), "")
    return first


def draft_skill(run: Any, *, title: str = "", unreviewed: bool = False) -> dict[str, str]:
    """A SKILL.md draft for the condition this run investigated.

    `unreviewed` switches the closing caveat: a draft on its way to an operator
    tells them what to prune before saving, which is nonsense once the same text
    has been saved automatically and is being read by the next investigation.
    """
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
    if unreviewed:
        lines += [
            "## How much to trust this",
            "",
            "- Written automatically when that investigation ended, and **reviewed by",
            "  nobody**. It is a lead, not a procedure.",
            "- Dead ends are **not** recorded: the run keeps which tools ran, not what",
            "  they returned, so a step listed above may have been a wasted one.",
            "- Anything specific to that day — hostnames, amounts, a one-off outage —",
            "  is noise here. Do not repeat it because it appears above.",
            "- If it is wrong, delete it: `DELETE /v1/skills/" + name + "`.",
            "",
        ]
    else:
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


def auto_install(
    run: Any,
    *,
    skills_dir: Path,
    limit: int,
    input_changes: tuple[str, ...] | list[str] = (),
) -> dict[str, Any]:
    """Write this run's runbook, or say why it was not written.

    Returns `{"installed": name}` or `{"skipped": reason}` — recorded on the run
    either way, because "the loop did nothing again" is the failure this whole
    feature exists to end, and it must not be invisible.
    """
    if input_changes:
        return {"skipped": "run changed its own inputs"}
    if getattr(run, "error", None):
        return {"skipped": "run failed"}
    if not str(getattr(run, "text", "") or "").strip():
        return {"skipped": "run produced no report"}

    draft = draft_skill(run, unreviewed=True)
    name = draft["name"]
    target = skills_dir / name

    if (target / "SKILL.md").is_file():
        # Deliberately not an update. See the module docstring: replacing a
        # runbook is an operator action, so that a later run — or a later
        # injection — cannot quietly rewrite one that was approved.
        return {"skipped": f"runbook '{name}' already exists"}
    try:
        existing = sorted(path.parent.name for path in skills_dir.glob("*/SKILL.md"))
    except OSError:
        existing = []
    if limit > 0 and len(existing) >= limit:
        # Not an eviction: something here may have been reviewed, and this is
        # not the code that gets to decide it is worth less than a new lead.
        return {"skipped": f"at the {limit}-runbook cap"}

    try:
        target.mkdir(parents=True, exist_ok=True)
        manifest = target / "SKILL.md"
        tmp = manifest.with_suffix(".tmp")
        tmp.write_text(draft["content"], encoding="utf-8")
        tmp.replace(manifest)
        # Provenance as data, not prose to be re-parsed: what wrote this, from
        # which investigation, when, and whether anyone has looked at it.
        (target / "origin.json").write_text(
            json.dumps(
                {
                    "written_by": "auto-distill",
                    "reviewed": False,
                    "session_key": str(getattr(run, "session_key", "")),
                    "run_id": str(getattr(run, "run_id", "")),
                    "engine_session_id": str(getattr(run, "engine_session_id", "") or ""),
                    "model": str(getattr(run, "model", "")),
                    "origin": str(getattr(run, "origin", "")) or "api",
                    "written_at": round(time.time(), 3),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        return {"skipped": f"write failed: {exc}"}
    return {"installed": name}
