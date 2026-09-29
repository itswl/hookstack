"""The investigator's console in two languages: every key its script builds from a prefix exists.

scripts/assert_design.py holds every board's two string sets to each other and
every key written out in a script to the set; the families this page BUILDS —
t("status." + run.status) — are named here.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

PAGE = Path(__file__).resolve().parents[1].joinpath("hookprobe", "ui.html").read_text(encoding="utf-8")
STRINGS = json.loads(re.search(r'<script type="application/json" id="strings">([\s\S]*?)</script>', PAGE).group(1))
SCRIPT = re.search(r"<script>([\s\S]*)</script>", PAGE).group(1)


def _table(name: str) -> list[str]:
    body = re.search(r"var " + name + r" = \[([^\]]*)\]", SCRIPT)
    assert body, f"the script no longer declares {name}"
    return re.findall(r'"([^"]+)"', body.group(1))


def test_every_key_built_from_a_prefix_exists_in_both_languages():
    families = {
        "wb.": _table("WB_ORDER"),
        "status.": ["running", "completed", "failed", "timeout", "stopped", "interrupted"],
        "act.status.": _table("ACT_STATUSES"),
        "inputs.": ["added", "removed", "edited"],
        "layer.": ["project", "user", "config"],
        "theme.": ["light", "dark"],
    }
    for lang in ("en", "zh"):
        missing = [p + m for p, members in families.items() for m in members if p + m not in STRINGS[lang]]
        assert missing == [], (lang, missing)


def test_the_links_already_out_there_still_open():
    """The pipe links a hop to its investigation with #session=<key>; skills and
    agents were linked as #skills/<name> and #agents/<name>. All still land."""
    assert "^session=(.+)$" in SCRIPT
    assert "^(skills|agents|audit)" in SCRIPT
