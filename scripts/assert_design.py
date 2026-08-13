#!/usr/bin/env python3
"""The three pages must look and behave like one product.

Each service ships a single self-contained page — no build step, no shared
asset, because a board that cannot render while another service is down is
not a board. The cost of that choice is duplication, and duplication drifts:
the investigator's console and the two ledgers had three palettes, three type
stacks and four independent poll timers between them.

So the shared parts are copied verbatim and this script is the contract. It
compares the delimited blocks byte for byte and fails loudly on drift, which
is cheaper than noticing months later that one page polls every 5 seconds.

    python3 scripts/assert_design.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PAGES = (
    Path("hookrelay/hookrelay/status.html"),
    Path("hookjudge/hookjudge/status.html"),
    Path("hookprobe/hookprobe/ui.html"),
)

# (label, first line of the block, last line of the block)
BLOCKS = (
    (
        "design tokens",
        "/* ── hookstack design tokens · keep this block identical in all three pages ── */",
        "/* ── end design tokens ────────────────────────────────────────────────────── */",
    ),
    (
        "refresh control markup",
        '<span class="rc">',
        "</span>",
    ),
    (
        "refresh control script",
        "// ── refresh control · keep this block identical in all three pages ─────────",
        "// ── end refresh control ───────────────────────────────────────────────────",
    ),
)


def extract(text: str, start: str, end: str) -> str | None:
    i = text.find(start)
    if i < 0:
        return None
    j = text.find(end, i)
    if j < 0:
        return None
    return re.sub(r"[ \t]+$", "", text[i : j + len(end)], flags=re.MULTILINE)


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    failures: list[str] = []
    for label, start, end in BLOCKS:
        found: dict[Path, str] = {}
        for page in PAGES:
            block = extract((root / page).read_text(encoding="utf-8"), start, end)
            if block is None:
                failures.append(f"{page}: no {label} block")
                continue
            found[page] = block
        if len(set(found.values())) > 1:
            failures.append(f"{label}: the pages disagree — " + ", ".join(str(p) for p in found))

    # The whole point of the refresh control: nothing may keep its own clock.
    # The only permitted setInterval is the one the control itself owns.
    for page in PAGES:
        for number, line in enumerate((root / page).read_text(encoding="utf-8").splitlines(), 1):
            if "setInterval(" in line and "refreshTimer = setInterval(" not in line:
                failures.append(f"{page}:{number}: a timer outside the refresh control — {line.strip()[:60]}")

    for line in failures:
        print(f"  FAIL  {line}")
    if failures:
        print(f"\n{len(failures)} design assertion(s) failed")
        return 1
    print(f"design: {len(BLOCKS)} shared blocks identical across {len(PAGES)} pages, one timer each")
    return 0


if __name__ == "__main__":
    sys.exit(main())
