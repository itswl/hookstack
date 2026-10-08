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

import json
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
    # Added 2026-09-29, when the judge's board and the investigator's console
    # were rebuilt on the pipe's layout. The palette had been one product for
    # weeks while the parts built from it were three: three kinds of button, two
    # kinds of list row, a window.confirm on one page and a modal on another, a
    # date printed three ways. The components, the dialog every page asks with,
    # and the helpers they are drawn by are now copied verbatim like the palette.
    (
        "components",
        "/* ── hookstack components · keep this block identical in all three pages ── */",
        "/* ── end components ─────────────────────────────────────────────────────── */",
    ),
    (
        "dialogs markup",
        "<!-- ── hookstack dialogs · keep this block identical in all three pages ── -->",
        "<!-- ── end dialogs ──────────────────────────────────────────────────────── -->",
    ),
    (
        "kit script",
        "// ── hookstack kit · keep this block identical in all three pages ──────────",
        "// ── end kit ───────────────────────────────────────────────────────────────",
    ),
    # Added 2026-10-06. The pipe's board got its install wiring first and the
    # other two were copied in by hand the day the operator put the
    # investigator's console on a home screen and found no icon. Pinned so the
    # third copy does not drift from the first two the way the token wiring did.
    (
        "install script",
        "// ── install on a phone · keep this block identical in all three pages ─────",
        "// ── end install ───────────────────────────────────────────────────────────",
    ),
)

# Whole files copied between the services, byte for byte. The service worker
# is one file because the three shells are one shape; the one page-specific
# fact — which path is the page — reaches it in the query of its own URL, from
# the page that registers it.
FILES = (("service worker", tuple(page.parent / "static" / "sw.js" for page in PAGES)),)

# The head, for the phone (2026-10-06, corrected 2026-10-08). Two theme-color
# metas split by the system's preference, so a browser's bar is right before
# any script runs; the install block rewrites both to the shown theme, because
# a media query cannot see a pick. An iOS home-screen app is another matter:
# it reads its status bar once, at launch, ignores theme-color changes after,
# and the `default` style follows the DEVICE's appearance, not the page's — a
# page switched to the other theme kept the old bar until relaunch. So the bar
# is translucent and the header draws under it in its own colour, the clock
# following the page's color-scheme (a painted dark strip was tried first and
# sat under a dark clock) — which needs viewport-fit=cover and the safe-area
# variables in the CSS: one without the other is a bar over the header, or
# insets that are all zero. The variables, not env() directly, because an
# installed app can report every inset as zero and the install block measures.
HEAD_MUST = (
    '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">',
    '<meta name="theme-color" media="(prefers-color-scheme: light)"',
    '<meta name="theme-color" media="(prefers-color-scheme: dark)"',
    '<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">',
)
HEAD_MUST_NOT = ('status-bar-style" content="default"',)
# The CSS half of viewport-fit=cover: what the page must say to clear the bar
# and paint under it. Checked in the whole page, not the head.
PAGE_MUST = (
    "--safe-top: env(safe-area-inset-top)",
    "padding-top: var(--safe-top)",
    "calc(18px + var(--safe-top))",
    "var(--safe-bottom)",
    "applySafeInsets",
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


def word_problems(text: str) -> list[str]:
    """A page's words: two languages with one set of keys and the same blanks,
    and nothing the script asks for that the set does not hold.

    At runtime a missing key falls back to English and then to the key itself,
    so a typo renders as `hero.waitng` on somebody's phone while every test that
    only loads the page stays green. What this reads is a key written out — as
    the argument, or as either arm of a ternary that is the argument. A key the
    script builds (`t("route." + name)`) or looks up in a table is the page's own
    tests' to check.
    """
    block = re.search(r'<script type="application/json" id="strings">([\s\S]*?)</script>', text)
    script = re.search(r"<script>([\s\S]*)</script>", text)
    if not block or not script:
        return ["a page needs its strings block and its script, and one of them is missing"]
    if block.start() > script.start():
        return ["the strings block must come before the script that reads it at load"]
    try:
        words = json.loads(block.group(1))
    except ValueError as error:
        return [f"the strings block is not JSON: {error}"]
    en, zh = words.get("en") or {}, words.get("zh") or {}
    problems = [
        f"{lang}.{key}: a word must be a string, not {type(value).__name__}"
        for lang, table in (("en", en), ("zh", zh))
        for key, value in table.items()
        if not isinstance(value, str)
    ]
    if problems:
        return problems
    if set(en) != set(zh):
        problems.append(f"the two languages differ in keys: {sorted(set(en) ^ set(zh))[:8]}")
    for key in sorted(set(en) & set(zh)):
        if set(re.findall(r"\{(\w+)\}", en[key])) != set(re.findall(r"\{(\w+)\}", zh[key])):
            problems.append(f"{key}: the two languages leave different blanks")
    asked: set[str] = set()
    code = script.group(1)
    written = re.findall(r'\b(t|th|tn|thn)\("([^"]+)"', code)
    written += [
        (fn, key)
        for fn, yes, no in re.findall(r'\b(t|th|tn|thn)\([^()"]*\?\s*"([^"]+)"\s*:\s*"([^"]+)"', code)
        for key in (yes, no)
    ]
    for fn, key in written:
        if not key.endswith("."):
            asked |= {key + ".one", key + ".other"} if fn in ("tn", "thn") else {key}
    asked |= set(re.findall(r'data-t[pt]?="([^"]+)"', text))
    missing = sorted(asked - set(en))
    if missing:
        problems.append(f"asked for and missing: {missing[:8]}")
    return problems


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
    # The investigator's console had two more — transient "saved" labels that
    # cleared themselves after 2.5s. They left on 2026-09-29 with the rebuild:
    # a save says so in a toast now, which leaves when its CSS animation ends.
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
    for label, copies in FILES:
        texts = {copy: (root / copy).read_text(encoding="utf-8") for copy in copies if (root / copy).exists()}
        for copy in copies:
            if copy not in texts:
                failures.append(f"{copy}: no {label}")
        if len(set(texts.values())) > 1:
            failures.append(f"{label}: the copies disagree — " + ", ".join(str(p) for p in texts))
    for page in PAGES:
        head = (root / page).read_text(encoding="utf-8").split("<body", 1)[0]
        for needle in HEAD_MUST:
            if needle not in head:
                failures.append(f"{page}: the head lacks {needle}")
        for needle in HEAD_MUST_NOT:
            if needle in head:
                failures.append(f"{page}: the head says {needle} — read once at launch, and it follows the device")
        whole = (root / page).read_text(encoding="utf-8")
        for needle in PAGE_MUST:
            if needle not in whole:
                failures.append(f"{page}: viewport-fit=cover without {needle} in the CSS")

    # The mode is chosen before the body paints, or a light system sees a dark
    # flash on every load; and no colour of a page's own may ignore the mode.
    theme_start = BLOCKS[5][1]
    for page in PAGES:
        text = (root / page).read_text(encoding="utf-8")
        for problem in word_problems(text):
            failures.append(f"{page}: {problem}")
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
        f"design: {len(BLOCKS)} shared blocks identical across {len(PAGES)} pages, {len(FILES)} shared file, "
        "heads carry the phone metas, both modes named through variables, both languages carry every word asked for, "
        "no unsanctioned timers"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
