#!/usr/bin/env python3
"""Every relative link in the docs resolves.

Cheap, and it catches the specific thing that breaks whenever files move:
a README pointing at a path that used to exist. Three separate moves in this
repo produced three dead links, each found by hand afterwards.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LINK = re.compile(r"\[[^\]]*\]\((?!https?://|mailto:)([^)]+)\)")


def main() -> int:
    dead: list[str] = []
    checked = 0
    for doc in sorted(ROOT.rglob("*.md")):
        if any(part in (".venv", "node_modules", ".git") for part in doc.parts):
            continue
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
