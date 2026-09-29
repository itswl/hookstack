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

Since the boards became pushed rather than polled, it also forbids timers.
setInterval is banned outright. setTimeout cannot be, because two honest uses
of it survive — so every setTimeout call must match a shape listed below, and
a new one is a conversation rather than a silent regression. See SANCTIONED.

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
        "live control markup",
        '<span class="rc">',
        "</span>",
    ),
    (
        "live control script",
        "// ── live control · keep this block identical in all three pages ────────────",
        "// ── end live control ──────────────────────────────────────────────────────",
    ),
    # Added 2026-08-21. Three services had three ways to type the same kind of
    # secret — an input box, a window.prompt, and a third localStorage naming
    # convention — because the earlier unification pass matched the palette and
    # never looked at the interaction. Pinning the palette and not this is why it
    # came apart a second time.
    (
        "token wiring",
        "/* ── hookstack token wiring · keep this block identical in all three pages ── */",
        "/* ── end token wiring ───────────────────────────────────────────────────── */",
    ),
    # Added 2026-09-08. Same palette, three navigation idioms: the pipe had
    # tabs, the judge a help link that unfolded a section, the investigator a
    # row of eight buttons. One tab strip, pinned, so the next page gets it for
    # free and the fourth idiom never appears.
    (
        "tab shell",
        "/* ── hookstack tab shell · keep this block identical in all three pages ── */",
        "/* ── end tab shell ──────────────────────────────────────────────────────── */",
    ),
    # Added 2026-09-29, with the light mode. The tokens carry both sets of
    # values; this snippet in <head> picks one before the first paint (what the
    # system asks for, unless the reader picked); the wiring is the button beside
    # refresh and the listener for a system that changes its mind. Pinned for the
    # palette's reason: three boards whose toggles behave three ways is the drift
    # this script exists to catch, and it would be the first thing a reader
    # moving between them noticed.
    (
        "theme first paint",
        "/* ── hookstack theme · keep this block identical in all three pages ── */",
        "/* ── end theme ───────────────────────────────────────────────────────── */",
    ),
    (
        "theme wiring",
        "// ── theme wiring · keep this block identical in all three pages ───────────",
        "// ── end theme wiring ──────────────────────────────────────────────────────",
    ),
)

# A colour written into a page's CSS as a literal does not change with the
# mode: it is the same grey on a white page as on a dark one. So outside the
# blocks that DEFINE variables (`:root { … }` and `:root[data-theme="light"]
# { … }`, the tokens among them), a page names its colours through var() —
# white is the one literal allowed, for text on a filled accent. Found on
# 2026-09-29 by making the light mode: eighteen literals on the investigator's
# console and fifteen on the pipe's board, each one a dark-only surface.
_VARIABLE_BLOCK = re.compile(r':root(?:\[data-theme="(?:light|dark)"\])?\s*\{[^}]*\}')
_LITERAL_COLOUR = re.compile(r"#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)|hsla?\([^)]*\)")
_ALLOWED_LITERALS = {"#fff", "#ffffff"}


def literal_colours(text: str) -> list[tuple[int, str]]:
    """Every colour literal in the page's <style> that is not a variable definition."""
    found: list[tuple[int, str]] = []
    for style in re.finditer(r"<style>([\s\S]*?)</style>", text):
        body = style.group(1)
        offset = text[: style.start(1)].count("\n")
        blanked = _VARIABLE_BLOCK.sub(lambda m: "\n" * m.group(0).count("\n"), body)
        blanked = re.sub(r"/\*[\s\S]*?\*/", lambda m: "\n" * m.group(0).count("\n"), blanked)
        for match in _LITERAL_COLOUR.finditer(blanked):
            if match.group(0).lower() not in _ALLOWED_LITERALS:
                found.append((offset + blanked[: match.start()].count("\n") + 1, match.group(0)))
    return found


# Every setTimeout the boards are allowed to contain, normalised the way
# timer_calls() normalises (whitespace collapsed to single spaces).
#
# An allowlist, and not a rule, because the two mechanisms cannot be told apart
# mechanically. Banning setInterval was enough right up until someone noticed
# that
#
#     function tick() { refresh(); setTimeout(tick, 5000); }
#
# is the same clock with a different spelling and passed this checker without
# comment. But the reconnect backoff below is ALSO a self-rescheduling
# setTimeout that calls the function it sits in — the difference is that it only
# re-arms from a .catch(), which no amount of line-matching can see. So the
# structural test was abandoned for an exact one: these shapes are fine, and
# anything else has to be read by a person and added here deliberately.
#
# The consequence to accept: retuning a sanctioned timer (2500 -> 3000) reddens
# this gate. That is the intended cost — the failure prints the fingerprint to
# paste, so it costs one line, and it means no timer changes unreviewed.
SANCTIONED = (
    # The live control's reconnect backoff, byte-identical in all three pages
    # (the "live control script" block above pins that). Armed only when the
    # stream drops, capped at ~32s, and cancelled when a newer connection
    # supersedes it — a board left open overnight coming back on its own, not a
    # board asking a question every N seconds.
    "setTimeout(function () { if (controller === liveAbort) liveConnect(url, headers, onChange); },"
    " Math.pow(2, liveRetry) * 500)",
    # Two transient "saved" labels on the investigator's console, which clear
    # themselves and schedule nothing. One-shot, fire and forget.
    'setTimeout(() => { $("#memStatus").textContent = ""; }, 2500)',
    'setTimeout(() => { $("#promptStatus").textContent = ""; }, 2500)',
)


def timer_calls(text: str, name: str) -> list[tuple[int, str]]:
    """Every `name(...)` call in text, as (line number, whole call collapsed).

    Whole call, not the line it starts on: the backoff's first line is the bare
    `setTimeout(function () {`, so a five-second refresh loop written in the
    same style would have been indistinguishable from it to a line-wise match
    and walked straight through the allowlist.

    Parens inside string literals are skipped, so a body containing "failed ("
    does not run the scan off the end. Anything this fails to balance ends up
    truncated, matches nothing, and reddens the gate — the safe direction for a
    checker to be wrong in.
    """
    found: list[tuple[int, str]] = []
    for match in re.finditer(re.escape(name) + r"\(", text):
        cursor = match.end() - 1
        depth, quote = 0, ""
        while cursor < len(text):
            char = text[cursor]
            if quote:
                if char == "\\":
                    cursor += 2
                    continue
                if char == quote:
                    quote = ""
            elif char in "\"'`":
                quote = char
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    break
            cursor += 1
        line = text[: match.start()].count("\n") + 1
        found.append((line, re.sub(r"\s+", " ", text[match.start() : cursor + 1])))
    return found


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

    # The mode is chosen before the body paints, or a light system sees a dark
    # flash on every load; and no colour of a page's own may ignore the mode.
    theme_start = BLOCKS[5][1]
    for page in PAGES:
        text = (root / page).read_text(encoding="utf-8")
        at, body = text.find(theme_start), text.find("<body")
        if at < 0 or body < 0 or at > body:
            failures.append(f"{page}: the theme snippet must sit in <head>, before the body paints")
        for number, colour in literal_colours(text):
            failures.append(
                f"{page}:{number}: the colour {colour} is written as a literal, so it stays the same in both "
                "modes — name it through a variable with a value in each theme"
            )

    # The whole point of the live control: these boards do not keep a clock at
    # all. What they show changes when the service writes, and the service says
    # so — a page that reintroduces a poll loop is answering a question nobody
    # is asking, at whatever interval its author guessed.
    for page in PAGES:
        text = (root / page).read_text(encoding="utf-8")
        for number, call in timer_calls(text, "setInterval"):
            failures.append(f"{page}:{number}: a poll loop — the boards are pushed now — {call[:70]}")
        for number, call in timer_calls(text, "setTimeout"):
            if call not in SANCTIONED:
                failures.append(
                    f"{page}:{number}: an unsanctioned timer — a recursive setTimeout is a poll loop "
                    f"wearing a different hat. If this one is a genuine one-shot, add it to SANCTIONED "
                    f"in this script:\n          {call}"
                )

    for line in failures:
        print(f"  FAIL  {line}")
    if failures:
        print(f"\n{len(failures)} design assertion(s) failed")
        return 1
    print(
        f"design: {len(BLOCKS)} shared blocks identical across {len(PAGES)} pages, both modes named through "
        "variables, no unsanctioned timers"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
