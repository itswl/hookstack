"""The board's words: two languages, one set of keys, none missing.

The page is one file with its strings in a JSON block, one set per language,
and a script that asks for them by key. Nothing at runtime says a key is
missing — `t()` falls back to English and then to the key itself, so a typo
renders as `hero.waitng` on somebody's phone and every test that only loads
the page stays green. These read the page as text and hold the two sets, and
the script's every request, to each other.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

PAGE = Path(__file__).resolve().parents[1].joinpath("hookrelay", "status.html").read_text(encoding="utf-8")
STRINGS = json.loads(re.search(r'<script type="application/json" id="strings">([\s\S]*?)</script>', PAGE).group(1))
SCRIPT = re.search(r"<script>([\s\S]*)</script>", PAGE).group(1)
KEYS = set(STRINGS["en"])


def _js_names(kind: str, name: str) -> list[str]:
    """The members of one of the script's constant tables, which some keys are built from."""
    if kind == "list":
        body = re.search(r"var " + name + r" = \[([^\]]*)\]", SCRIPT)
        assert body, f"the script no longer declares {name}"
        return re.findall(r'"([^"]+)"', body.group(1))
    body = re.search(r"var " + name + r" = \{([^}]*)\}", SCRIPT)
    assert body, f"the script no longer declares {name}"
    return re.findall(r'(?:^|[{,])\s*"?([a-z_]+)"?\s*:', body.group(1))


def test_both_languages_carry_the_same_keys_and_the_same_blanks():
    assert set(STRINGS) == {"en", "zh"}
    assert set(STRINGS["en"]) == set(STRINGS["zh"])
    for key in KEYS:
        en, zh = STRINGS["en"][key], STRINGS["zh"][key]
        assert en.strip() and zh.strip(), key
        assert set(re.findall(r"\{(\w+)\}", en)) == set(re.findall(r"\{(\w+)\}", zh)), key


def test_every_key_the_page_asks_for_exists():
    asked: set[str] = set()
    for fn, key in re.findall(r'\b(t|th|tn|thn)\("([^"]+)"', SCRIPT):
        if key.endswith("."):
            continue  # a prefix; its members are checked below
        asked |= {key + ".one", key + ".other"} if fn in ("tn", "thn") else {key}
    asked |= set(re.findall(r'data-t[pt]?="([^"]+)"', PAGE))
    # A key in a ternary or a table, e.g. t(dirty ? "rt.dirty" : "rt.clean").
    namespaces = {key.split(".")[0] for key in KEYS}
    for literal in re.findall(r'"([a-z_]+(?:\.[a-z0-9_]+)+)"', SCRIPT):
        if literal.split(".")[0] in namespaces and literal + ".one" not in KEYS:
            asked.add(literal)
    families = {
        "stage.": _js_names("list", "STAGES"),
        "state.": _js_names("obj", "STATUS_CLS"),
        "press.": [*_js_names("obj", "PRESS_KINDS"), "pressed"],
        "quiet.": [*_js_names("obj", "QUIET_CODES"), "other"],
    }
    for prefix, members in families.items():
        assert members, prefix
        asked |= {prefix + member for member in members}
    assert sorted(asked - KEYS) == []


def test_the_page_speaks_chinese_to_a_chinese_browser_and_remembers_a_choice():
    assert "localStorage.getItem(LANG_KEY)" in SCRIPT and "/^zh/i.test(navigator.language" in SCRIPT
    assert STRINGS["zh"]["tab.alerts"] == "告警" and STRINGS["en"]["tab.alerts"] == "Alerts"
