"""The judge's board in two languages: every key its script builds from a prefix exists.

scripts/assert_design.py holds every board's two string sets to each other and
every key written out in a script to the set. What it cannot see is a key the
script BUILDS — t("route." + name) — so the families this page builds are named
here, from the tables they are built from.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

PAGE = Path(__file__).resolve().parents[1].joinpath("hookjudge", "status.html").read_text(encoding="utf-8")
STRINGS = json.loads(re.search(r'<script type="application/json" id="strings">([\s\S]*?)</script>', PAGE).group(1))
SCRIPT = re.search(r"<script>([\s\S]*)</script>", PAGE).group(1)


def _table(name: str) -> list[str]:
    body = re.search(r"var " + name + r" = \[([^\]]*)\]", SCRIPT)
    assert body, f"the script no longer declares {name}"
    return re.findall(r'"([^"]+)"', body.group(1))


def test_every_key_built_from_a_prefix_exists_in_both_languages():
    families = {
        "route.": _table("ROUTES"),
        "imp.": [*_table("IMPORTANCE"), "recovered"],
        "theme.": ["light", "dark"],
        "tile.": ["judged", "spend", "dead", "agree", "repeats", "mattered"],
    }
    for lang in ("en", "zh"):
        missing = [p + m for p, members in families.items() for m in members if p + m not in STRINGS[lang]]
        assert missing == [], (lang, missing)


def test_the_board_reads_like_the_other_two():
    """The shared layout, in the markup: one sentence first, a drawer per verdict,
    the settings behind the gear, and the older links still landing."""
    for marker in ('id="heroMain"', 'id="drawer"', 'id="settings"', 'data-view="verdicts"', 'data-view="review"'):
        assert marker in PAGE, marker
    assert 'h === "help" || h === "review"' in SCRIPT, "the #help and #review links other pages send still open"
