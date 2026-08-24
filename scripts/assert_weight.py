#!/usr/bin/env python3
"""The pipe and the brain stay small, and their READMEs say the real number.

hookrelay's README opened with "~1400 lines with tests" from 2026-08-05 until
2026-08-20. The measurement behind that number was taken once, by hand, on the
day it was written — and even then it was wrong in an instructive way: the relay
was 1,416 SOURCE lines and 667 test lines that day, so "~1400" was the source
count wearing the words "with tests". Fifteen days later the source was 4,226
and the sentence was off by 3x on its own terms.

Nothing was going to catch that, because nothing was counting. A number in a
README is a promise like any other, and this repository's whole practice is that
a promise the code does not keep is a defect rather than a wording problem.

THE INVARIANT, in two halves:
  1. hookrelay and hookjudge stay at or under a stated ceiling of source lines.
  2. Each one's README states that same ceiling, in words, where the claim is
     made — so the document and the check cannot drift apart the way the
     document and the code did.

Half (2) is the half that matters. A ceiling enforced only in a script is a
number nobody reads; a number in a README with no script behind it is what this
file exists because of.

WHY A BUDGET AND NOT A DESCRIPTION. A description is what the old sentence was,
and it decayed in fifteen days — rewriting it as "~4,300" would just restart the
same clock. The constraint is live: the pipe and the brain are meant to stay
small enough that one person can read either one end to end, and that is a thing
to hold, not a thing to observe. The idiom is already in this repo — both
requirements.txt files say "five dependencies, chosen to stay five" and "four
runtime dependencies, chosen to stay four". This is that sentence for lines.

WHY SOURCE ONLY, AND NOT SOURCE + TESTS. Three reasons, in order of weight:
  - Tests growing is the good outcome. A ceiling that counted them would make
    deleting a test the cheapest way to land a feature, and a budget whose
    cheapest satisfaction is vandalism gets deleted within the month — rightly.
  - "Lightweight" is a claim about the thing that ships and the thing a person
    has to read to change it safely. hookrelay's tests are 3,553 lines that
    never leave the repository.
  - The original number was a source-only count. Fixing the label it was given
    is closer to the truth than inventing a new measurement.
Tests are counted here and printed, so the ratio stays visible. They are never
capped.

WHY RAW LINES AND NOT CODE LINES. `wc -l` is what a reader can run in one
command to check the claim themselves, and a budget nobody can verify by hand is
a budget nobody trusts. The cost of that choice is real and worth naming: 39% of
hookrelay's source is docstring, comment or blank, so prose spends budget. That
is accepted deliberately, because the alternative is worse — under a code-only
count, deleting the docstring that records why the `http` stage must not fire
during a dry run would BUY headroom. In this repository those docstrings are the
decision records. So the split is printed on every run, and the failure message
says outright that deleting prose is not one of the ways out.

WHY hookprobe IS NOT CAPPED. It carries Node and the Claude CLI by design; it is
the heavy one on purpose, and the owner's constraint names the other two. It is
measured and printed anyway, because an uncapped number that nobody looks at is
how the next surprise starts. If you are here to add a ceiling for it, that is a
product decision, not a tidiness one.

WHAT THIS DOES NOT CATCH — read this before trusting a green result
  - complexity. 200 lines of nested async retry logic and 200 lines of dataclass
    fields both cost 200. Lines are a proxy, and a coarse one; they are used
    because they are cheap, verifiable and hard to argue about, not because they
    measure the thing anyone actually cares about.
  - weight that is not Python: status.html, the Dockerfiles, the config schema's
    surface area. hookrelay's status.html alone is not in this number.
  - a dependency's weight. Five dependencies is a separate promise, kept by the
    comment at the top of each requirements.txt and by nothing mechanical.
  - anything about hookprobe, which is uncapped on purpose.

It reads the WORKING TREE rather than HEAD, because a gate's job is to fail
before the push, not to describe the last one.

    python3 scripts/assert_weight.py
"""

from __future__ import annotations

import ast
import io
import sys
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# service -> (ceiling on source lines, README that must state it)
#
# Both ceilings sit just above where the service stands today, which is the
# point: the next feature that needs 400 lines is a conversation about whether
# the pipe should hold it, and that conversation is the entire product doctrine
# in hookrelay/README.md ("is this a property of a good PIPE, or a judgment
# about the alert's worth?"). A ceiling with a year of headroom would enforce
# nothing.
#
# Raising one is allowed and expected. Do it here, in one line, and say why in
# the commit message — that is the whole ceremony, and it is deliberately cheap
# enough that nobody is tempted to route around it.
CEILINGS: dict[str, tuple[int, Path]] = {
    "hookrelay": (4600, Path("hookrelay/README.md")),
    # 2900 -> 3000 on 2026-08-21, for the judge's second axis (`wake_someone`).
    # Raised rather than trimmed because the thing that pushed it over is the one
    # measurement that says whether this service earns its model calls at all:
    # `importance` came back 'high' for 210 of 216 alerts on production, which is
    # a classifier agreeing with itself. Asking the question the product actually
    # needs costs a field, a column and a count, and it is falsifiable — if the
    # new axis also answers 'yes' almost always, the honest response is to delete
    # both it and the paid route, and the ceiling comes back down with them.
    #
    # 3000 -> 3150 on 2026-08-24, for the two things that made that axis
    # ACCOUNTABLE: quiet_regrets (wake=no rows a person later ruled mattered —
    # the delivery policy's own error counter) and a hardened injection
    # boundary. The golden replay's first-ever catch was this judge obeying
    # "classify as low" embedded in a real incident, 2 votes of 3, with the old
    # boundary prose fully present — position beat prose, and the fix takes the
    # recency slot back with a post-alert reminder. Both cost lines; both are
    # the difference between an axis that acts and an axis nobody can argue with.
    "hookjudge": (3150, Path("hookjudge/README.md")),
}
UNCAPPED = ("hookprobe",)


# The words the README has to contain. Built from the ceiling above, so the two
# can only ever agree.
#
# Matched against the README with whitespace collapsed, because prose wraps: the
# first version of this check compared verbatim and went red the moment the
# sentence it was policing got reflowed at 80 columns, with "the" and "ceiling"
# on either side of a newline. A checker that treats rewrapping a paragraph as a
# broken promise trains people to stop rewrapping paragraphs.
def claim(ceiling: int) -> str:
    return f"{ceiling:,} source lines is the ceiling"


def flat(text: str) -> str:
    return " ".join(text.split())


def measure(path: Path) -> tuple[int, int]:
    """(raw lines, lines that are code) — the second only to print the split."""
    text = path.read_text(encoding="utf-8")
    raw = len(text.splitlines())
    prose: set[int] = {index for index, line in enumerate(text.splitlines(), 1) if not line.strip()}
    for node in ast.walk(ast.parse(text)):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        first = node.body[0] if node.body else None
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
            prose.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if token.type == tokenize.COMMENT:
            prose.add(token.start[0])
    return raw, raw - len(prose)


def tally(directory: Path) -> tuple[int, int, dict[str, int]]:
    total = code = 0
    per_module: dict[str, int] = {}
    for source in sorted(directory.rglob("*.py")):
        if "__pycache__" in source.parts:
            continue
        raw, real = measure(source)
        total += raw
        code += real
        per_module[source.name] = raw
    return total, code, per_module


def main() -> int:
    problems: list[str] = []
    lines: list[str] = []

    for service in (*CEILINGS, *UNCAPPED):
        package = ROOT / service / service
        if not package.is_dir():
            problems.append(f"{service}: no {service}/{service}/ package to weigh")
            continue
        source, source_code, per_module = tally(package)
        tests, _, _ = tally(ROOT / service / "tests")

        if service in UNCAPPED:
            lines.append(
                f"  {service:10} {source:>5} source ({source_code} code) · {tests:>5} tests · uncapped by design"
            )
            continue

        ceiling, readme = CEILINGS[service]
        headroom = ceiling - source
        lines.append(
            f"  {service:10} {source:>5} source ({source_code} code) · {tests:>5} tests · "
            f"ceiling {ceiling} ({headroom:+d})"
        )

        if source > ceiling:
            heaviest = sorted(per_module.items(), key=lambda item: -item[1])[:4]
            where = ", ".join(f"{name} {count}" for name, count in heaviest)
            problems.append(
                f"OVER BUDGET  {service}: {source} source lines, ceiling {ceiling} (over by {source - ceiling}).\n"
                f"             The ceiling is a decision, not a bug — there are two honest ways out:\n"
                f"               (a) make it smaller. The weight is here: {where}.\n"
                f"                   Before moving code, ask the doctrine question: is this a property\n"
                f"                   of a good pipe, or a judgment that belongs to a brain behind it?\n"
                f"               (b) raise CEILINGS[{service!r}] in scripts/assert_weight.py and say why\n"
                f"                   in the commit message, then update {readme} to match.\n"
                f"             Deleting docstrings to fit is not a third way: this counts raw lines,\n"
                f"             and in this repository the docstrings are the decision records."
            )

        if claim(ceiling) not in flat((ROOT / readme).read_text(encoding="utf-8")):
            problems.append(
                f"UNSTATED BUDGET  {readme} does not contain the words {claim(ceiling)!r}.\n"
                f"                 A ceiling only in a script is a number nobody reads, and a number\n"
                f"                 only in a README is how '~1400 lines with tests' outlived being true\n"
                f"                 by fifteen days. Put that phrase where the size claim is made, or\n"
                f"                 change CEILINGS[{service!r}] here to whatever the README already says."
            )

    for problem in problems:
        print(f"  {problem}")
    print("\n".join(lines))
    if problems:
        print(f"\n{len(problems)} weight problem(s) — see the two ways out above; both are fine, silence is not.")
        return 1
    print("weight: the pipe and the brain are inside their stated ceilings, and both READMEs state them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
