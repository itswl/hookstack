#!/usr/bin/env python3
"""Every service's CI tests on the interpreter its image ships.

hookprobe/Dockerfile moved to python:3.14-slim while .github/workflows/
ci-hookprobe.yml kept testing on 3.12 — and release.yml's gate matrix kept a
3.12 entry, under a comment reading "Keep each entry equal to that service's
Dockerfile FROM". Both files said "test what ships" beside a pin that tested
something else, and nothing read a Dockerfile and a workflow together.

    python3 scripts/assert_python_pins.py

For each of hookrelay, hookjudge and hookprobe the Dockerfile's FROM is the
one truth; the service's ci file and the release gate matrix must agree with
it. The versions differ BETWEEN services on purpose — hookprobe's image also
carries Node and the Claude CLI and moved later — so this asserts agreement
per service, never uniformity.

WHAT IT DOES NOT CHECK — read this before trusting a green result
  - the prose. ci-hookprobe.yml's comment said "Dockerfile: python:3.12-slim"
    while the Dockerfile said 3.14, and no script can read intent.
  - release.yml's docker build legs: images are built FROM their Dockerfiles,
    so there is no pin there to disagree.
  - pins in files this parse does not know. The counts below fail loudly when
    an expected pin goes missing — the false-green shape this repository has
    paid for twice — but a NEW workflow pinning a version elsewhere is not
    seen until this list learns about it.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SERVICES = ("hookrelay", "hookjudge", "hookprobe")
CI_FILE = {
    "hookrelay": ".github/workflows/ci.yml",
    "hookjudge": ".github/workflows/ci-hookjudge.yml",
    "hookprobe": ".github/workflows/ci-hookprobe.yml",
}
RELEASE = ".github/workflows/release.yml"

FROM = re.compile(r"^FROM\s+python:([0-9]+\.[0-9]+)")
PIN = re.compile(r'python-version:\s*"([0-9]+\.[0-9]+)"')
MATRIX_ENTRY = re.compile(r"^\s*-\s*service:\s*(\S+)\s*$")
MATRIX_PYTHON = re.compile(r'^\s*python:\s*"([0-9]+\.[0-9]+)"\s*$')


def pin_lines(path: Path, pattern: re.Pattern[str]) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        match = pattern.search(line)
        if match:
            found.append((number, match.group(1)))
    return found


def release_matrix() -> dict[str, tuple[int, str]]:
    """service -> (line, version) from release.yml's pin, read across the file.

    Both matrices in that file name services; only the gate matrix also names
    a python version, so the pairs found here can only be its entries."""
    found: dict[str, tuple[int, str]] = {}
    current: str | None = None
    for number, line in enumerate((ROOT / RELEASE).read_text(encoding="utf-8").splitlines(), start=1):
        entry = MATRIX_ENTRY.match(line)
        if entry:
            current = entry.group(1)
            continue
        pinned = MATRIX_PYTHON.match(line)
        if pinned and current:
            found[current] = (number, pinned.group(1))
            current = None
    return found


def main() -> int:
    problems: list[str] = []
    checked = 0
    matrix = release_matrix()
    for service in SERVICES:
        froms = pin_lines(ROOT / service / "Dockerfile", FROM)
        if len(froms) != 1:
            problems.append(f"{service}/Dockerfile: expected exactly one `FROM python:` line, found {len(froms)}")
            continue
        truth_line, truth = froms[0]
        checked += 1
        pins = pin_lines(ROOT / CI_FILE[service], PIN)
        if len(pins) != 1:
            problems.append(f"{CI_FILE[service]}: expected exactly one `python-version:` pin, found {len(pins)}")
        else:
            number, version = pins[0]
            if version != truth:
                problems.append(
                    f"{CI_FILE[service]}:{number} tests on {version}, "
                    f"but {service}/Dockerfile:{truth_line} ships {truth}"
                )
        entry = matrix.get(service)
        if entry is None:
            problems.append(f"{RELEASE}: no gate-matrix entry for {service} — the entry is gone or this parse lost it")
        else:
            number, version = entry
            if version != truth:
                problems.append(f"{RELEASE}:{number} gates {service} on {version}, but its Dockerfile ships {truth}")
    if problems:
        print("python pins: a workflow tests on an interpreter the image does not ship:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        print(
            "\nMove the pin to the Dockerfile's version — or move the image, deliberately — "
            "and keep the comment and the pin saying the same thing.",
            file=sys.stderr,
        )
        return 1
    print(f"python pins: {checked} services test on the interpreter their image ships")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
