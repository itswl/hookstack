#!/usr/bin/env python3
"""Every door and every knob is either written down or named as deliberately not.

A doc audit found four statements that had become false and five features nobody
had written down at all. The audit was a throwaway script; this is that script,
kept, so the enumerable half of doc drift stops being something a person has to
remember to look for.

WHAT THIS CAN AND CANNOT DO, because the difference is the whole design

Three of those four false statements were PROSE ABOUT BEHAVIOUR:

    "the draft lands as proposal.md and waits for review"
    "memory suggestions have no switch"
    "restore is just the PUT"

Nothing mechanical can check those. There is no expression over the source that
says whether a paragraph is still true, and a check that claimed to would be the
worst kind — a green light over a stale sentence.

The fourth was ENUMERABLE: "Five kinds exist", against a tuple of six. That class
is checkable, and so is the larger class it belongs to — a route or an env var
that exists in code and appears in no document. This file checks exactly that
class and says so, rather than implying the docs are true.

The other half of the answer is not a script and belongs in the READMEs: prose
should describe INTENT, which changes rarely, and leave MECHANISM to the
docstring beside the code, which cannot drift from it. Every false statement
above was a README restating a mechanism that a docstring also described. Two
copies of one claim, and the one further from the code rots first.

    python3 scripts/assert_docs.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

SERVICES = ("hookrelay", "hookjudge", "hookprobe")
# A route documented on the service's own operator page counts as documented.
PAGES = (
    Path("hookrelay/hookrelay/status.html"),
    Path("hookjudge/hookjudge/status.html"),
    Path("hookprobe/hookprobe/ui.html"),
)

# Routes that exist and are deliberately not written up. Each one is a decision,
# not an oversight, and the reason is here so the next audit does not re-find it.
ROUTES_UNWRITTEN: dict[str, str] = {
    "/v1/remediations": "the console's own list view; the approve/reject pair below is what an operator is told about",
    "/v1/remediations/{proposal_id}/approve": "documented as behaviour under the remediation gate, not as a path to call",
    "/v1/remediations/{proposal_id}/reject": "same",
    "/v1/memory/suggestions": "list view behind the memory page",
    "/v1/memory/suggestions/{suggestion_id}/accept": "the page and the card are the paths a person uses",
    "/v1/memory/suggestions/{suggestion_id}/dismiss": "same",
    "/v1/skills/{name}/proposal": "reachable only while a pre-2026-08-21 draft is still parked",
    "/v1/skills/{name}/proposal/approve": "same — consolidation applies itself now",
    "/v1/skills/{name}/proposal/reject": "same",
    "/v1/agents/{name}": "the roles are documented as a concept; the CRUD pair is console plumbing",
}

# Infrastructure knobs. The repo has evidently chosen not to enumerate host,
# port, path and tuning limits in prose, and this records that choice rather than
# reversing it in a commit about stale docs.
ENV_UNWRITTEN = re.compile(
    r"_(?:HOST|PORT|DB|WORKER_INTERVAL|MAX_BODY_BYTES|BREAKER_COOLDOWN_SECONDS"
    r"|AI_FIELDS_LIMIT|AI_TITLE_LIMIT|BURST_WINDOW_SECONDS"
    r"|ALARM_URL|ALARM_MIN_INTERVAL_SECONDS)$"
)

# (label, the tuple that defines the set, the doc that has to list all of it).
# For facts a document must restate because a reader needs them in one place —
# duplicated on purpose, so pinned. "Five kinds exist" over a tuple of six is
# what this line exists to prevent.
ENUMERATED = (
    ("card action kinds", "hookrelay/hookrelay/actions.py", "KINDS", Path("hookrelay/docs/configuration.md")),
    ("ruling verdicts", "hookprobe/hookprobe/rulings.py", "VERDICTS", Path("hookprobe/README.md")),
)

_PLACEHOLDER = re.compile(r"\{[^}]+\}")
_ROUTE = re.compile(r'@app\.(?:get|post|put|delete)\("([^"]+)"')
_ENV = re.compile(r'os\.environ\.get\("([A-Z_]+)"|_(?:int|float|flag)\("([A-Z_]+)"')


def _normalised(text: str) -> str:
    """Placeholders collapsed, so `{source_name}` and `{source}` are one path."""
    return _PLACEHOLDER.sub("{}", text)


def _corpus() -> str:
    docs = [p for p in Path().rglob("*.md") if ".venv" not in str(p)]
    return _normalised("\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in [*docs, *PAGES]))


def _tuple_members(path: str, name: str) -> list[str]:
    """The strings in a module-level tuple, read as text — importing three
    services into one process is a worse dependency than a regex."""
    src = Path(path).read_text(encoding="utf-8")
    match = re.search(rf"^{name}\s*=\s*\((.*?)\)", src, re.S | re.M)
    return re.findall(r'"([^"]+)"', match.group(1)) if match else []


def main() -> int:
    corpus = _corpus()
    problems: list[str] = []

    for service in SERVICES:
        source = "\n".join(f.read_text(encoding="utf-8") for f in Path(service, service).glob("*.py"))
        for route in sorted(set(_ROUTE.findall(source))):
            if _normalised(route) in corpus or route in ROUTES_UNWRITTEN:
                continue
            problems.append(f"{service}: route {route} appears in no document and is not in ROUTES_UNWRITTEN")
        for a, b in sorted(set(_ENV.findall(source))):
            name = a or b
            if name in corpus or ENV_UNWRITTEN.search(name):
                continue
            problems.append(f"{service}: {name} appears in no document and is not an infrastructure knob")

    for label, module, name, doc in ENUMERATED:
        members = _tuple_members(module, name)
        if not members:
            problems.append(f"{label}: could not read {name} from {module}")
            continue
        text = doc.read_text(encoding="utf-8")
        absent = [m for m in members if f"`{m}`" not in text]
        if absent:
            problems.append(f"{label}: {doc} does not list {absent} (the tuple has {len(members)})")

    if problems:
        print("docs have fallen behind:", file=sys.stderr)
        for line in problems:
            print(f"  {line}", file=sys.stderr)
        print(
            "\nWrite it up, or add it to ROUTES_UNWRITTEN in this file with the reason it does\n"
            "not need writing up. Both are fine; silence is what produced a README describing\n"
            "a review gate that had been removed.\n\n"
            "This check cannot tell you whether a PARAGRAPH is still true. Prose about\n"
            "behaviour belongs in the docstring beside the code, and a README that restates a\n"
            "mechanism has made a second copy that will rot.",
            file=sys.stderr,
        )
        return 1

    print(
        f"docs: every route and knob across {len(SERVICES)} services is written up or named as not, "
        f"{len(ENUMERATED)} enumerated sets match their tuples"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
