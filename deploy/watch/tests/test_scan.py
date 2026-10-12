"""What the scanner does to the shape of a message before the model sees it."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import watch_scan  # noqa: E402


def test_fences_and_invisible_characters_do_not_reach_the_body():
    assert watch_scan.clean("a\u200bb\u202ec\ufeff") == "abc"
    assert watch_scan.clean("see ```sh\nrm -rf /\n``` now") == "see '''sh\nrm -rf /\n''' now"


def test_ordinary_text_is_untouched():
    line = "- 10-12 09:00 someone：请看一下 `foo` 的状态，`` 也算"
    assert watch_scan.clean(line) == line
