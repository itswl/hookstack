#!/usr/bin/env python3
"""This repository is PUBLIC. Nothing in it may name a real estate.

Ported from WebhookWise on 2026-08-24, after a manual sweep found a real
production alert rule name (its threshold included), a real Grafana folder and a
real platform hostname across nine test fixtures here — and the same names all
through this repository's history. Fixture realism is how estate names spread:
they look like test data, so nothing scans them.

Placeholders must be obviously fictional. `示例…` and `demo-…` are; a real
project name is not, and neither is a team handle somebody can @-mention.

    python3 scripts/assert_no_estate_identifiers.py

THE PATTERN LIST IS NOT IN THIS REPOSITORY. Carrying the patterns inline would
make this file the last public copy of exactly the words the scrub removed —
together with the replacement for each, which is a decoding table. The mechanism
is public; the list is not.

The list lives in `.estate-identifiers` (git-ignored, format in
`.estate-identifiers.example`), and in CI in a repository secret written to that
path before this runs. Set ESTATE_GUARD_REQUIRED=1 there: without it a missing
list would make this exit 0 and the protection would evaporate silently, which
is the failure mode the guard exists to prevent.

Add a pattern the moment a real name gets scrubbed, or the scrub decays into a
one-off cleanup that the next paste undoes.
"""

from __future__ import annotations

import os
import re
import subprocess  # nosec B404
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PATTERN_FILE = Path(os.environ.get("ESTATE_PATTERNS_FILE", ROOT / ".estate-identifiers"))

# The list is external, so nothing in this repository can tell whether the copy
# CI just wrote is the CURRENT one. It can tell whether it is implausibly SHORT.
#
# That is the failure this catches, and it is not hypothetical: patterns were
# added locally on 2026-08-21 and the repository secret was not updated, so CI
# went on passing against a list two rules out of date while local enforcement
# was strict. A count is not an identifier, so it can live here in the open.
#
# A ratchet, like the font-size bound: raise it when patterns are added; never
# lower it to make a red build green. Forgetting to raise it costs protection,
# not a false failure.
MIN_PATTERNS = 28


def load_patterns() -> tuple[tuple[str, str], ...] | None:
    """(regex, why it must not appear) from the un-tracked list. None if absent."""
    if not PATTERN_FILE.is_file():
        return None
    rules: list[tuple[str, str]] = []
    for raw in PATTERN_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip("\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        pattern, _, reason = line.partition("\t")
        if not pattern or not reason:
            continue  # a line without a tab cannot say why, so it is not a rule
        rules.append((pattern, reason.strip()))
    return tuple(rules)


# The list is external now, so this file no longer needs exempting from itself.
EXEMPT: set[str] = {".estate-identifiers.example"}


def scanned_tree() -> str:
    """WHICH tree the verdict is about — always the working tree at ROOT.

    Stated in the output because it cannot be inferred from it. On 2026-09-12
    this script was run inside four detached worktrees checked out at release
    tags, to find out which tags carried an estate identifier, and it printed a
    clean verdict for all four — while one of those tags held a real project
    name. `ROOT` is this file's own location and `tracked_files()` runs
    `git ls-files` with `cwd=ROOT`, so it had scanned the same working tree
    four times and said so to nobody.

    The capability to scan an arbitrary ref is NOT added here; what is added is
    the check declining to be misread. A tool that cannot look somewhere should
    name where it did look, rather than leave a reader to assume it obeyed them.
    """
    try:
        head = subprocess.run(  # nosec B603 B607
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        head = "unknown"
    return f"the working tree at {ROOT.name}@{head}"


def tracked_files() -> list[str]:
    # Fixed argv, no shell, no untrusted input: the only "input" is the
    # repository itself. `git` is resolved from PATH, as everywhere else here.
    out = subprocess.run(  # nosec B603 B607
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout
    return [line for line in out.splitlines() if line]


def untracked_files() -> list[str]:
    """New files not yet added and not ignored: what the NEXT commit could take.

    2026-09-22: a new test fixture carrying a real project name sat untracked
    while the local gate ran `git ls-files`, printed a clean verdict over 440
    tracked files, and the file was added and committed a minute later. CI,
    which sees only tracked files at a commit, caught it — after the push. The
    gate exists to catch this BEFORE the push, so it has to look at what a
    commit could carry, not only at what the last one did. Ignored files stay
    out: `.env`, `work-data/`, `data/watch/` hold real names on purpose.
    """
    out = subprocess.run(  # nosec B603 B607
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [line for line in out.splitlines() if line]


def unpushed_messages() -> list[tuple[str, str]] | None:
    """(short sha, message) for every commit the next push would publish, or
    None when there is no upstream ref to measure against.

    The file scan cannot see a commit message, and on 2026-09-22 the project
    name that sat in the fixture also sat in the message of the commit that
    added it — the file would have been fixed by hand and the message would
    have stayed public. Messages leave the machine on push, so the boundary is
    upstream..HEAD. When that range cannot be computed the caller SAYS so;
    a clean scan of nothing is the failure this whole file is about.
    """
    for ref in ("@{upstream}", "origin/main"):
        probe = subprocess.run(  # nosec B603 B607
            ["git", "rev-parse", "--verify", "--quiet", ref], cwd=ROOT, capture_output=True, text=True
        )
        if probe.returncode == 0:
            break
    else:
        return None
    out = subprocess.run(  # nosec B603 B607
        ["git", "log", "--format=%h%x00%B%x01", f"{ref}..HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    records: list[tuple[str, str]] = []
    for record in out.split("\x01"):
        if "\x00" in record:
            sha, body = record.split("\x00", 1)
            records.append((sha.strip(), body))
    return records


def main() -> int:
    rules = load_patterns()
    # An EMPTY list is as useless as a missing one, and far easier to end up with:
    # CI writes the file from a secret, so an unset secret produces a zero-byte
    # file, zero rules, and a cheerful "clean" over every file in the repository.
    # Both cases take the same branch.
    if not rules:
        required = os.environ.get("ESTATE_GUARD_REQUIRED") == "1"
        state = "no pattern list at" if rules is None else "an empty pattern list at"
        if required:
            print(f"  FAIL  {state} {PATTERN_FILE} and ESTATE_GUARD_REQUIRED=1")
            print("        CI must write the list from its secret before this runs;")
            print("        an unset secret writes an empty file, which is this failure.")
            return 1
        print(f"  SKIP  {state} {PATTERN_FILE}")
        print("        copy .estate-identifiers.example and fill in the real names.")
        return 0
    if len(rules) < MIN_PATTERNS:
        print(f"  FAIL  the pattern list has {len(rules)} rule(s); this repository expects at least {MIN_PATTERNS}")
        print(f"        {PATTERN_FILE} is stale or truncated. In CI that means the")
        print("        ESTATE_IDENTIFIERS secret was not updated after patterns were added:")
        print("        gh secret set ESTATE_IDENTIFIERS --repo <owner>/<repo> < .estate-identifiers")
        return 1

    compiled = [(re.compile(pattern, re.I), reason) for pattern, reason in rules]
    problems: list[str] = []

    files = tracked_files()
    seen = set(files)
    extra = [name for name in untracked_files() if name not in seen]
    for name in files + extra:
        if name in EXEMPT:
            continue
        path = ROOT / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary or unreadable: nothing to read identifiers out of
        for line_no, line in enumerate(text.splitlines(), start=1):
            for pattern, reason in compiled:
                if pattern.search(line):
                    problems.append(f"{name}:{line_no}: {reason}")

    messages = unpushed_messages()
    for sha, body in messages or []:
        for pattern, reason in compiled:
            if pattern.search(body):
                problems.append(f"commit {sha} message: {reason}")

    for problem in problems:
        print(f"  FAIL  {problem}")
    if problems:
        print(f"\n{len(problems)} estate identifier(s) in a public repository")
        return 1
    # The rule count is the only evidence the list arrived intact. It is read
    # from a TAB-separated file, and CI writes that file from a secret somebody
    # pasted: a paste that loses tabs drops those rules silently, because a line
    # without a tab cannot say why it is forbidden and is skipped. Printing the
    # count turns "I hope the secret is right" into something a log can answer.
    # Every set examined is named beside the verdict, including the one that
    # could not be: a reader who assumes messages were scanned when they were
    # not is the reader this line exists for.
    scanned_messages = (
        "commit messages NOT scanned (no upstream ref here)"
        if messages is None
        else f"{len(messages)} unpushed commit message(s)"
    )
    print(
        f"no estate identifiers: {len(files)} tracked + {len(extra)} untracked file(s) in {scanned_tree()}, "
        f"{scanned_messages}, clean against {len(rules)} rule(s)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
