"""Turn a finished investigation into a runbook the next one can use.

The investigator is already told to read prior case files, and the skills
directory is described as what previous runs distilled — but nothing ever wrote
one, so the loop was open at exactly the step that would make the next
investigation cheaper.

Two ways to close it, and the difference is *who writes*, not whether it is
automatic:

* `draft_skill` assembles a draft and returns it. `POST /v1/runs/{key}/distill`
  hands it to an operator, who saves it with `PUT /v1/skills/{name}`.
* `auto_write` does the same assembly and writes it, at the end of a run,
  **from the service** — which is the point. The agent's own Write and Edit
  cannot reach `.claude/` (see hookprobe.inputs): an agent that can edit its
  future instructions mid-run is one injected line away from teaching itself
  forward, and nothing in the record would say so. The service writing a
  runbook assembled from the run's own structure is a different act with a
  different failure mode, and it is the one that can be made safe.

A runbook **updates itself**: the second investigation of the same condition
adds to the first rather than replacing it. Replacing would be regression
dressed as learning — a shallow run would flatten a runbook that had already
seen five incidents, because a run only knows its own steps. So each
investigation appends a case, newest first, and everything already in the file
survives it.

That is also what keeps an operator's corrections. Neither side is restricted:
a run may write a runbook a person edited, and a person may edit a runbook a
run wrote. The invariant is not *who may write* but that **no write destroys
what was there** — automatic writes only insert into the case region, anything
outside it is carried through untouched, and every write snapshots the previous
manifest into `history/` first, so a bad one (from either side) is one file copy
away from being undone.

The rest of the shape:

* assembled from the record, never from free text the run chose: the question,
  the tool sequence, the conclusion;
* never from a run that failed or produced nothing — there is no lesson in a
  run that did not finish;
* never from a run that changed its own inputs: that one already misbehaved,
  and what it wants to teach forward is the thing being defended against;
* the case region is trimmed to the most recent cases, because a runbook that
  grows without bound is prefix cost on every later run;
* stamped with where each revision came from, and marked unreviewed in its own
  text after a machine write, so neither the next run nor the next operator
  mistakes it for doctrine.

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
        f'description: What previous investigations of "{headline[:90]}" checked, and what they found.',
        "---",
        "",
        f"# {headline[:120]}",
        "",
        # The seam. Later investigations of the same condition insert here,
        # newest first; everything around it survives them, which is where an
        # operator's own corrections are safe to live.
        "## Investigations",
        "",
        CASES_MARKER,
        "",
        case_block(run, steps=steps, conclusion=conclusion, at=time.time()),
    ]
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
            "- Later investigations of the same thing add a case above, newest",
            "  first. Anything you write outside that list — here, or in a section",
            "  of your own — survives every one of them.",
            "- If it is wrong, delete it: `DELETE /v1/skills/" + name + "`. Earlier",
            "  versions are in `history/`.",
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


# The seam an automatic write is allowed to touch. Everything outside it —
# frontmatter, title, the trust caveat, and whatever an operator has added —
# is carried through every update unread.
CASES_MARKER = "<!-- hookprobe:cases -->"
_CASE_RE = re.compile(r"<!-- case:start [^\n>]*-->.*?<!-- case:end -->\n*", re.DOTALL)
_HISTORY_KEEP = 8


def case_block(run: Any, *, steps: list[str], conclusion: str, at: float) -> str:
    """One investigation, delimited so a later trim can find its edges."""
    stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(at))
    session = str(getattr(run, "session_key", "") or "?")
    lines = [
        f"<!-- case:start {int(at)} -->",
        f"### {stamp} · session `{session}`",
        "",
    ]
    if steps:
        lines += [f"{index}. `{step}`" for index, step in enumerate(steps[:30], 1)]
    else:
        lines.append("_No tools ran — the answer came from the alert alone._")
    lines += ["", conclusion or "_(the run left no conclusion)_", "", "<!-- case:end -->", ""]
    return "\n".join(lines)


def merge_case(existing: str, block: str, *, max_cases: int) -> str:
    """Insert one case at the top of the case region. Destroys nothing else.

    A missing marker means the file was written before this existed, or an
    operator restructured it. Either way the answer is to append rather than to
    guess at its shape.
    """
    if CASES_MARKER in existing:
        head, _, tail = existing.partition(CASES_MARKER)
        merged = f"{head}{CASES_MARKER}\n\n{block}{tail.lstrip(chr(10))}"
    else:
        merged = f"{existing.rstrip()}\n\n## Later investigations\n\n{CASES_MARKER}\n\n{block}"

    if max_cases > 0:
        found = list(_CASE_RE.finditer(merged))
        # Newest first, so the tail is what falls off.
        for match in reversed(found[max_cases:]):
            merged = merged[: match.start()] + merged[match.end() :]
    return merged


def auto_write(
    run: Any,
    *,
    skills_dir: Path,
    limit: int,
    max_cases: int = 5,
    input_changes: tuple[str, ...] | list[str] = (),
) -> dict[str, Any]:
    """Leave this run's findings in a runbook — new, or added to an existing one.

    Returns `{"installed": name}`, `{"updated": name}` or `{"skipped": reason}`
    — recorded on the run either way, because "the loop did nothing again" is
    the failure this whole feature exists to end, and it must not be invisible.
    """
    if input_changes:
        return {"skipped": "run changed its own inputs"}
    if getattr(run, "error", None):
        return {"skipped": "run failed"}
    if not str(getattr(run, "text", "") or "").strip():
        return {"skipped": "run produced no report"}

    turns = list(getattr(run, "turns", []) or [])
    headline = _headline(turns, str(getattr(run, "current_message", "") or "")) or run.session_key
    name = slug(headline)
    target = skills_dir / name
    manifest = target / "SKILL.md"
    existed = manifest.is_file()

    if not existed:
        try:
            present = sorted(path.parent.name for path in skills_dir.glob("*/SKILL.md"))
        except OSError:
            present = []
        if limit > 0 and len(present) >= limit:
            # Only new runbooks are capped. An existing one going on learning
            # costs a case, not a whole new prefix entry — and refusing that
            # would be exactly the restriction this loop is meant not to have.
            return {"skipped": f"at the {limit}-runbook cap"}

    now = time.time()
    try:
        target.mkdir(parents=True, exist_ok=True)
        if existed:
            snapshot(target, manifest)
            content = merge_case(
                manifest.read_text(encoding="utf-8"),
                case_block(run, steps=_steps(turns), conclusion=_conclusion(turns), at=now),
                max_cases=max_cases,
            )
        else:
            content = draft_skill(run, unreviewed=True)["content"]
        tmp = manifest.with_suffix(".tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(manifest)
        record_revision(
            target,
            by="auto-distill",
            reviewed=False,
            at=now,
            detail={
                "session_key": str(getattr(run, "session_key", "")),
                "run_id": str(getattr(run, "run_id", "")),
                "engine_session_id": str(getattr(run, "engine_session_id", "") or ""),
                "model": str(getattr(run, "model", "")),
                "origin": str(getattr(run, "origin", "")) or "api",
            },
        )
    except OSError as exc:
        return {"skipped": f"write failed: {exc}"}
    return {"updated" if existed else "installed": name}


def snapshot(target: Path, manifest: Path) -> None:
    """Keep the manifest as it stood, so any write can be undone.

    The reason updates need no permission check: nothing is lost when the last
    good version is still on the volume. Applies to operator writes too — a
    correction typed into the wrong runbook is the same accident.
    """
    history = target / "history"
    history.mkdir(exist_ok=True)
    # Two writes in the same second must not share a filename — the second
    # snapshot would silently overwrite the first, losing exactly the version
    # a quick save-then-restore was relying on. Borrow the next free second.
    stamp = int(time.time())
    while (history / f"{stamp}-SKILL.md").exists():
        stamp += 1
    (history / f"{stamp}-SKILL.md").write_text(manifest.read_text(encoding="utf-8"), encoding="utf-8")
    stale = sorted(history.glob("*-SKILL.md"))[:-_HISTORY_KEEP]
    for path in stale:
        path.unlink(missing_ok=True)


def record_revision(target: Path, *, by: str, reviewed: bool, at: float, detail: dict[str, Any] | None = None) -> None:
    """Append to the runbook's provenance — who wrote it, when, reviewed or not.

    Provenance as data, not prose to be re-parsed: the skills page has to be
    able to say which runbooks nobody has looked at, and that is exactly the
    claim a heuristic over the text would get wrong.
    """
    path = target / "origin.json"
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(record, dict):
            record = {}
    except (OSError, ValueError):
        record = {}
    revisions = record.get("revisions")
    revisions = revisions if isinstance(revisions, list) else []
    revisions.append({"by": by, "at": round(at, 3), **(detail or {})})
    record.update(
        {
            "written_by": by,
            "reviewed": reviewed,
            "written_at": round(at, 3),
            "revisions": revisions[-20:],
            **(detail or {}),
        }
    )
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ── consolidation: from a case pile to a curated procedure ────────────────────

CONSOLIDATION_MESSAGE = """You are consolidating an investigation runbook, not investigating.

Read the runbook at {path} (and, if useful, the case files it cites under
/data/results/). It has accumulated {count} cases — repeated investigations of
the same condition. Rewrite it as ONE curated procedure:

- keep the YAML frontmatter, keeping `name: {name}` exactly;
- keep the `<!-- hookprobe:cases -->` marker line, and below it keep ONLY the
  newest case block (`<!-- case:start -->` … `<!-- case:end -->`) as the most
  recent worked example — future investigations append there;
- above the marker, distill what the cases agree on: the checks that decided
  it, in order, with the commands; the usual conclusion; what varied between
  cases and what that variation meant;
- keep any operator-written sections (they are the reviewed truth) verbatim;
- drop day-specific noise: hostnames-of-the-day, amounts, timestamps.

Output ONLY the complete new file content, starting with the `---` frontmatter
line. No commentary, no code fences."""


def case_count(manifest_text: str) -> int:
    return manifest_text.count("<!-- case:start")


def valid_consolidation(text: str, name: str) -> str:
    """The draft, normalized — or empty when it cannot be trusted as a manifest.

    The model was told to output only the file; this checks it listened.
    Anything that fails here is dropped with a logged reason rather than
    parked as a proposal an operator would have to read to reject.
    """
    cleaned = text.strip()
    # A fenced block despite the instruction is common enough to unwrap.
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\n", "", cleaned)
        cleaned = re.sub(r"\n```\s*$", "", cleaned)
        cleaned = cleaned.strip()
    if not cleaned.startswith("---"):
        return ""
    if f"name: {name}" not in cleaned[:300]:
        return ""
    if CASES_MARKER not in cleaned:
        return ""
    return cleaned + ("\n" if not cleaned.endswith("\n") else "")


def write_proposal(skill_dir: Path, content: str) -> None:
    tmp = (skill_dir / "proposal.md").with_suffix(".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(skill_dir / "proposal.md")
