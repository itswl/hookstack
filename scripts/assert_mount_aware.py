#!/usr/bin/env python3
"""Every board addresses its own service relative to where it is mounted.

Since 2026-09-28 a gateway serves the family under one address with each
console under a prefix, so a page that fetches "/status" instead of
BASE + "/status" works on its own port and breaks inside the one site — and
nothing but a browser notices. The judge's board shipped exactly that for an
hour: its fetches were prefixed with BASE and BASE was never declared, which
node --check cannot see. This reads the three pages and fails on either half
missing: a request or link that starts at the root, or a BASE used but never
declared.

    python3 scripts/assert_mount_aware.py
"""

from __future__ import annotations

import re
from pathlib import Path

PAGES = (
    Path("hookrelay/hookrelay/status.html"),
    Path("hookjudge/hookjudge/status.html"),
    Path("hookprobe/hookprobe/ui.html"),
)
# A request or a link the page builds from the root of the ORIGIN. The hash
# links (#journey=…) and the pinned live-control block (which takes its url
# from the caller) are not requests. The investigator's `api(path)` helper is
# the one place that page prefixes, so its calls carry root paths on purpose
# and are not matched; a raw fetch must carry BASE itself.
_ROOTED = re.compile(r"""(?:fetch|readJSON)\(\s*["'`]/[a-z]""")
_ROOTED_HREF = re.compile(r"""href=(?:"|\\"|')/(?:v1|trace|audit|status|timeline|ui)\b""")
_DECLARED = re.compile(r"(?:var|const) BASE = location\.pathname")
_USED = re.compile(r"\bBASE\b")


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    failures: list[str] = []
    for page in PAGES:
        text = (root / page).read_text(encoding="utf-8")
        script = text[text.find("<script>") :]
        rooted = [m.group(0) for m in _ROOTED.finditer(script)] + [m.group(0) for m in _ROOTED_HREF.finditer(script)]
        if rooted:
            failures.append(
                f"{page}: {len(rooted)} request(s) or link(s) start at the origin's root: {', '.join(rooted[:3])}"
            )
        used = len(_USED.findall(script))
        if used and not _DECLARED.search(script):
            failures.append(f"{page}: BASE is used {used} time(s) and never declared")
        if not used:
            failures.append(f"{page}: no BASE at all — this page cannot live under a prefix")
    if failures:
        print("\n".join("  FAIL  " + f for f in failures))
        return 1
    print(f"mount-aware: {len(PAGES)} boards address their service relative to where they are mounted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
