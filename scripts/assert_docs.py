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
    "/v1/remediations/{proposal_id}/approve": (
        "documented as behaviour under the remediation gate, not as a path to call"
    ),
    "/v1/remediations/{proposal_id}/reject": "same",
    "/v1/memory/suggestions": "list view behind the memory page",
    "/v1/memory/suggestions/{suggestion_id}/accept": "the page and the card are the paths a person uses",
    "/v1/memory/suggestions/{suggestion_id}/dismiss": "same",
    "/v1/skills/{name}/proposal": "reachable only while a pre-2026-08-21 draft is still parked",
    "/v1/skills/{name}/proposal/approve": "same — consolidation applies itself now",
    "/v1/skills/{name}/proposal/reject": "same",
    "/v1/agents/{name}": "the roles are documented as a concept; the CRUD pair is console plumbing",
}

# Environment variables are no longer checked here. `gen_reference.py --check`
# owns them: it generates the table from the dataclass, so a knob cannot be
# missing, and it fails when one has no `#` comment to describe it. That is a
# stronger guarantee than "the name appears in some README" — and it does not
# require a second copy of the fact, which is what rotted in the first place.
# (label, the tuple that defines the set, the doc that has to list all of it).
# For facts a document must restate because a reader needs them in one place —
# duplicated on purpose, so pinned. "Five kinds exist" over a tuple of six is
# what this line exists to prevent.
ENUMERATED = (
    (
        "card action kinds",
        "hookrelay/hookrelay/actions.py",
        "KINDS",
        Path("hookrelay/docs/configuration.md"),
    ),
    # Moved with the table that states it, when hookprobe's README stopped being
    # its own reference. Still a hand-written document, which is what makes this
    # check mean something — pointing it at a GENERATED file would be asking the
    # generator whether it generated.
    (
        "ruling verdicts",
        "hookprobe/hookprobe/rulings.py",
        "VERDICTS",
        Path("hookprobe/docs/configuration.md"),
    ),
)

# A fourth class, found by an external audit repeating an error it had read
# here: a prose COUNT. "Four routes" sat in judge.py's own header, hookjudge's
# README (twice — once above a table that already had five rows), OVERVIEW.md
# and both front pages, for weeks after rule-reuse made it five. ENUMERATED
# checks that every member is listed; it cannot see a sentence that miscounts
# them. This reads the number out of every "N routes" / "N条…路由" in the files
# pinned below and compares it with the ROUTE_* constants that define the set.
# Pinned files rather than the whole tree, so an unrelated "two routes" in some
# other document cannot trip it — every match in these files IS about the
# judge's cost routes, verified when the check was written.
_ROUTE_CONST = re.compile(r'^ROUTE_[A-Z_]+ = "', re.MULTILINE)
ROUTE_CONSTANTS = Path("hookjudge/hookjudge/contract.py")
ROUTE_COUNT_STATED = (
    Path("README.md"),
    Path("OVERVIEW.md"),
    Path("docs/index.md"),
    Path("docs/zh/index.md"),
    Path("hookjudge/README.md"),
    Path("hookjudge/hookjudge/judge.py"),
)
_COUNT_EN = re.compile(
    r"\b(one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s+routes\b",
    re.IGNORECASE,
)
_COUNT_ZH = re.compile(r"([一二三四五六七八九十]|\d+)条[^。\n]{0,12}?(?:路由|路径)")

# The same rule for the containment table, which drifted exactly the way this
# check exists to stop: a boundary was added, the table grew to fifteen rows,
# and three of the four places that state the count still said fourteen while
# the fourth said fifteen. Nobody reading either number could tell which was
# true, and the number is the claim the security page is FOR.
BOUNDARY_TABLE = Path("docs/containment.md")
# And every row must be SORTED, not merely present. The page gained a section
# on 2026-09-11 splitting the table into what holds against a hostile model,
# what only reduces accidents, what is a config-load invariant, and what is
# named as a residual — borrowed from NousResearch/hermes-agent's SECURITY.md,
# whose §2.2/§2.4 split is the thing this table was missing. A classification
# nothing checks is prose, and prose about a count is exactly what rotted one
# paragraph above it ("twelve" against a script carrying seventeen).
_CLASSIFIED_ROW = re.compile(r"^- \*\*(.+?)\*\*", re.MULTILINE)
# `_BOUNDARY_ROW` below counts rows and captures nothing; the names need their own.
_BOUNDARY_NAME = re.compile(r"^\| \*\*(.+?)\*\*", re.MULTILINE)
BOUNDARY_COUNT_STATED = (
    Path("README.md"),
    Path("README.zh-CN.md"),
    Path("docs/index.md"),
    Path("docs/zh/index.md"),
)
_BOUNDARY_ROW = re.compile(r"^\| \*\*", re.MULTILINE)
# Deliberately NOT an enumeration of the number words. It was one until
# 2026-09-10, and it stopped at twenty-five while `_TEENS` learned twenty-six:
# both English pages then said "twenty-six structural boundaries", matched
# nothing, and were silently excluded from the check that exists to keep them
# honest — the report still read "26 boundaries match every page that counts
# them" while counting one page of three. Match ANY word here and resolve it
# below, so an unknown word is a failure with a name instead of a page quietly
# dropping out of the set.
_BOUNDARY_EN = re.compile(
    r"\b([a-z]+(?:-[a-z]+)?|\d+)\s+structural\s+(?:security\s+)?boundaries\b",
    re.IGNORECASE,
)
_BOUNDARY_ZH = re.compile(r"([一二三四五六七八九十]{1,3}|\d+)条结构性边界")
_TEENS = {
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "十一": 11,
    "十二": 12,
    "十三": 13,
    "十四": 14,
    "十五": 15,
    "十六": 16,
    "十七": 17,
    "十八": 18,
    "十九": 19,
}
# Twenty through thirty-nine, composed rather than typed. This map was extended
# by hand at twenty-six, twenty-seven, twenty-eight and again at thirty — and the
# time it was caught the checker at least FAILED on the word it could not read
# rather than silently matching nothing. Composing the tens removes the chore
# instead of paying it once more per boundary; the next ten is one more tuple.
_UNITS_EN = ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine")
_UNITS_ZH = "一二三四五六七八九"
for _ten, _word, _char in ((20, "twenty", "二十"), (30, "thirty", "三十")):
    _TEENS[_word] = _TEENS[_char] = _ten
    _TEENS.update({f"{_word}-{unit}": _ten + i for i, unit in enumerate(_UNITS_EN, 1)})
    _TEENS.update({f"{_char}{unit}": _ten + i for i, unit in enumerate(_UNITS_ZH, 1)})
_NUMBER_WORDS = {
    **{
        w: i
        for i, w in enumerate(
            (
                "one",
                "two",
                "three",
                "four",
                "five",
                "six",
                "seven",
                "eight",
                "nine",
                "ten",
            ),
            1,
        )
    },
    **{w: i for i, w in enumerate("一二三四五六七八九十", 1)},
}


def _stated_boundaries(text: str) -> list[tuple[int, int | None, str]]:
    """Every (line number, stated boundary count, word) in the text.

    The count is None when the word is not a number this file knows. That is a
    failure the caller reports, never a match it drops: a page whose count word
    goes unrecognised is a page nobody is checking.
    """
    out: list[tuple[int, int | None, str]] = []
    for pattern in (_BOUNDARY_EN, _BOUNDARY_ZH):
        for m in pattern.finditer(text):
            word = m.group(1).lower()
            value = _TEENS.get(word) or _NUMBER_WORDS.get(word)
            if value is None:
                value = int(word) if word.isdigit() else None
            out.append((text.count("\n", 0, m.start()) + 1, value, word))
    return out


def _stated_counts(text: str) -> list[tuple[int, int]]:
    """Every (line number, stated count) in the text, both languages."""
    out = []
    for pattern in (_COUNT_EN, _COUNT_ZH):
        for m in pattern.finditer(text):
            word = m.group(1).lower()
            out.append(
                (
                    text.count("\n", 0, m.start()) + 1,
                    _NUMBER_WORDS.get(word) or int(word),
                )
            )
    return out


_PLACEHOLDER = re.compile(r"\{[^}]+\}")
_ROUTE = re.compile(r'@app\.(?:get|post|put|delete)\("([^"]+)"')

# The third member of the family in the header, and the one this check was
# missing. A route is a surface an operator calls; an env var is a surface an
# operator sets; a YAML key is a surface an operator WRITES, and hookrelay's is
# the largest of the three — a config file is how every deployment in this
# repository is wired. It went unchecked and three keys had accumulated with no
# mention anywhere, two of them replay-protection knobs that other documents
# already told people to rely on.
#
# Read from the loader rather than the dataclass: `item.get("name")` is the
# thing that makes a key user-facing, and a dataclass field with no loader line
# is internal state that nobody can set.
_YAML_KEY = re.compile(r'item\.get\(\s*"([a-z][a-z0-9_]*)"')
CONFIG_LOADERS = ((Path("hookrelay/hookrelay/config.py"), Path("hookrelay/docs/configuration.md")),)
# Keys that are deliberately not in the reference, with the reason.
KEYS_UNWRITTEN = {
    "name": "every section has one; naming it as a key would be noise",
    "type": "documented per channel type rather than as a key",
}


def _normalised(text: str) -> str:
    """Placeholders collapsed, so `{source_name}` and `{source}` are one path."""
    return _PLACEHOLDER.sub("{}", text)


# `docs/reference.md` is GENERATED from the routes and the settings dataclass, so
# it lists every door and knob by construction. Counting it as documentation would
# make this whole check vacuous — verified by adding an endpoint nothing mentions,
# watching this fail, regenerating, and watching it pass. Matched on the banner
# rather than the path so a generated file that moves is still excluded.
GENERATED = "generated by scripts/gen_reference.py"


def _corpus() -> str:
    docs = [
        p
        for p in Path().rglob("*.md")
        if ".venv" not in str(p) and GENERATED not in p.read_text(encoding="utf-8", errors="ignore")[:400]
    ]
    return _normalised("\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in [*docs, *PAGES]))


def _tuple_members(path: str, name: str) -> list[str]:
    """The strings in a module-level tuple, read as text — importing three
    services into one process is a worse dependency than a regex."""
    src = Path(path).read_text(encoding="utf-8")
    match = re.search(rf"^{name}\s*=\s*\((.*?)\)", src, re.DOTALL | re.MULTILINE)
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

    for loader, doc in CONFIG_LOADERS:
        text = doc.read_text(encoding="utf-8")
        for key in sorted(set(_YAML_KEY.findall(loader.read_text(encoding="utf-8")))):
            if key in KEYS_UNWRITTEN or f"`{key}`" in text or f"{key}:" in text:
                continue
            problems.append(f"{loader.parent.parent.name}: config key `{key}` appears in no document")

    for label, module, name, doc in ENUMERATED:
        members = _tuple_members(module, name)
        if not members:
            problems.append(f"{label}: could not read {name} from {module}")
            continue
        text = doc.read_text(encoding="utf-8")
        absent = [m for m in members if f"`{m}`" not in text]
        if absent:
            problems.append(f"{label}: {doc} does not list {absent} (the tuple has {len(members)})")

    defined = len(_ROUTE_CONST.findall(ROUTE_CONSTANTS.read_text(encoding="utf-8")))
    stated = 0
    for doc in ROUTE_COUNT_STATED:
        for line, count in _stated_counts(doc.read_text(encoding="utf-8")):
            stated += 1
            if count != defined:
                problems.append(
                    f"{doc}:{line} says {count} routes, but {ROUTE_CONSTANTS} defines {defined} ROUTE_* constants"
                )

    rows = len(_BOUNDARY_ROW.findall(BOUNDARY_TABLE.read_text(encoding="utf-8")))
    table_text = BOUNDARY_TABLE.read_text(encoding="utf-8")
    named = [n.strip() for n in _BOUNDARY_NAME.findall(table_text)]
    classified = [n.strip() for n in _CLASSIFIED_ROW.findall(table_text)]
    unsorted_rows = [n for n in named if n not in classified]
    phantom = [n for n in classified if n not in named]
    duplicated = sorted({n for n in classified if classified.count(n) > 1})
    for name in unsorted_rows:
        problems.append(
            f"{BOUNDARY_TABLE}: boundary {name!r} is in the table and in none of the four lists under "
            '"What holds, and what only helps" — say whether it holds against a hostile model'
        )
    for name in phantom:
        problems.append(f"{BOUNDARY_TABLE}: {name!r} is classified but is not a row in the table")
    for name in duplicated:
        problems.append(f"{BOUNDARY_TABLE}: {name!r} is classified more than once; a row belongs to one list")
    for doc in BOUNDARY_COUNT_STATED:
        for line, count, word in _stated_boundaries(doc.read_text(encoding="utf-8")):
            if count is None:
                problems.append(
                    f"{doc}:{line} states the boundary count as {word!r}, which this checker cannot read — "
                    f"add it to _TEENS, or this page stops being checked"
                )
            elif count != rows:
                problems.append(f"{doc}:{line} says {count} structural boundaries, but {BOUNDARY_TABLE} lists {rows}")

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
        f"docs: every route, knob and config key across {len(SERVICES)} services is written up or named as not, "
        f"{len(ENUMERATED)} enumerated sets match their tuples, "
        f"{stated} stated route counts all equal the {defined} defined, "
        f"{rows} containment boundaries match every page that counts them"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
