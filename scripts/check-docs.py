#!/usr/bin/env python3
"""Every relative link in the docs resolves.

Cheap, and it catches the specific thing that breaks whenever files move:
a README pointing at a path that used to exist. Three separate moves in this
repo produced three dead links, each found by hand afterwards.
"""

from __future__ import annotations

import re
import subprocess  # nosec B404 — one `git ls-files`, to check the repo and not the workspace
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LINK = re.compile(r"\[[^\]]*\]\((?!https?://|mailto:)([^)]+)\)")


def _docs() -> list[Path]:
    """The markdown this repository actually contains.

    Asks git rather than walking the filesystem, because a working tree holds
    things the repository does not: `data/` is gitignored and a runtime adapter
    probe left a vendored skill tree in it, whose internal links are none of
    this gate's business. A gate that fails on files nobody committed is a gate
    people learn to argue with.

    Falls back to a walk when git cannot answer — a released tarball has no
    index, and a link check is still worth running there.
    """
    try:
        listed = subprocess.run(  # nosec B603 B607 — fixed argv, no input
            ["git", "-C", str(ROOT), "ls-files", "-z", "*.md"],
            capture_output=True,
            check=True,
            timeout=30,
        )
        paths = [ROOT / name for name in listed.stdout.decode().split("\0") if name]
    except (OSError, subprocess.SubprocessError):
        paths = list(ROOT.rglob("*.md"))
    return sorted(
        path
        for path in paths
        if path.is_file() and not any(part in (".venv", "node_modules", ".git") for part in path.parts)
    )


def main() -> int:
    dead: list[str] = []
    checked = 0
    for doc in _docs():
        for match in LINK.finditer(doc.read_text(encoding="utf-8")):
            target = match.group(1).split("#")[0].strip()
            if not target:
                continue
            checked += 1
            if not (doc.parent / target).resolve().exists():
                dead.append(f"{doc.relative_to(ROOT)} -> {target}")

    for entry in dead:
        print(f"DEAD LINK  {entry}")
    print(f"checked {checked} relative links in docs: {len(dead)} dead")
    return 1 if dead else 0


if __name__ == "__main__":
    sys.exit(main())
