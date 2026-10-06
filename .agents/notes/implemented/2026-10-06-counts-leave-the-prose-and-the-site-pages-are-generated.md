---
title: Counts leave the prose, and the site's pages are generated
status: implemented
date: 2026-10-06
scope: stack
---

## Decision

No sentence states how many containment boundaries or judge routes there are.
The front pages say "the structural boundaries, each written up with what it
does not stop" and link to the table; the judge's pages say "the routes" and
list them. The two checkers that policed those numbers — `_stated_counts`,
`_stated_boundaries` and the number-word table in two languages behind them —
are deleted from `scripts/assert_docs.py`. What stays is the check that every
containment row is sorted into one of the four lists.

The docs site's two front pages (`docs/index.md`, `docs/zh/index.md`) are
generated from the two READMEs by `scripts/gen_front_pages.py`: the H1 and
badges go, a front-matter block and the site's language switch come in, links
into `docs/` lose their prefix, service links become names, and the tail is the
site's own. The gate and CI run it with `--check`. Two front pages are written
by hand now, not four.

## Why

The operator asked whether the shape had grown too complex. This was the
clearest case: a number that had to be kept equal in five places, in two
languages, had grown a checker, then number words up to thirty-nine in both
languages, then a bug where the Chinese README was the one page the checker
did not read. The number was never the claim — the table is — and a sentence
that says "twenty-nine" is wrong the day a row moves and says nothing a reader
can act on. Four copies of a front page is the same shape one level up: the
mirror rule ("delete stale prose in every mirror") was a human running a
generator by hand.

## Consequences

- Edit `README.md` or `README.zh-CN.md`, run `python3 scripts/gen_front_pages.py`,
  commit both. The gate fails on a stale page. Never edit the site's pages.
- The Chinese README now shows the same `curl` quick start as the site, and
  both READMEs carry the bridge-protocol link the site always had.
- A boundary added or merged changes the table and nothing else. The route
  list in the judge's README is still pinned to the ROUTE_* constants by name
  (`ENUMERATED`), which is the check with teeth.
