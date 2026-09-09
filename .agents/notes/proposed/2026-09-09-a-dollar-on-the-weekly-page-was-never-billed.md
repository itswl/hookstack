---
title: The weekly page says priced, not billed — the shadow arms disproved a label nobody had questioned
status: proposed
date: 2026-09-09
scope: stack
---

## Decision

`scripts/cost_report.py` no longer calls any dollar figure measured or billed.
Counts stay **measured** (verdicts, runs, hops, latency, rulings — read off a
ledger, no price table involved). Dollars are **priced**, on their own bullet,
naming what they were priced from. The sentence most likely to be quoted out of
context now reads "Priced at $X … neither is a bill".

Not done: changing any price constant, or trying to compute a truer figure.
Settling the number needs the gateway's own price list, which nothing in this
repository or on either host has.

## Why

**The label was disproved by measurement, not by argument.** Retiring the
shadow arms produced the evidence as a by-product: `hookjudge` and
`hookjudge-b` ran the same model over the same 53 events with an identical
route split — 31 paid calls each — and reported $0.0146 and $0.0127. Identical
traffic cannot cost two different amounts. The 15% gap is entirely their
per-1k constants (`0.0002/0.0012` against `0.00028/0.00042`), which are
operator-set env knobs multiplied by token counts in `judge.py`. Whichever is
nearer the truth, at least one was wrong all along, and the page carrying the
live one said "billed".

**The investigator's figure has the same shape and it was already written
down.** `hookprobe/docs/cost.md` has said since the OTel work that `cost_usd`
"prices Claude models" and is "the shape of the bill, not the bill, for a model
it does not know the price of" — this stack reaches a GPT-class model through an
Anthropic-dialect gateway. So the page was adding one unconfirmed price table to
another and presenting the sum as a bill. The doctrine for this already exists
one layer down: the pi adapter refuses to report a zero with tokens behind it,
because "a runtime that reports a number is more dangerous than one that reports
nothing". A page inherits that duty from the numbers it prints.

**Three labels rather than a caveat at the bottom.** A footnote is read once and
the figure is quoted forever. Splitting the bullet is what makes the distinction
survive being skimmed: the reader sees that the count and the dollar came from
different kinds of evidence, in the same glance that gives them the dollar.

**Naming the constants, not just the doubt.** The judge's bullet names
`HOOKJUDGE_AI_PRICE_IN_PER_1K` and `..._OUT_PER_1K`, so a reader who does have
the gateway's rates can check the arithmetic instead of re-deriving where it
came from. "Unverified" without a pointer is a shrug.

## Consequences

**The budget breaker is denominated in the same unconfirmed price.** It reads
the investigator's `cost_usd`, so "$7.39 of $10.00 spent" inherits whatever the
CLI's table is wrong by. An over-estimate trips the breaker early, which is
safe; an under-estimate lets an unattended node spend past its ceiling, which is
not. The direction is unestablished, and that is the strongest argument for
getting the real price list — stronger than the weekly page, which only informs.

**Nothing downstream changes shape.** `--json` keys are untouched; only prose
and bullet structure moved, so any consumer of the JSON is unaffected.

**The day the price list arrives, this is a two-line change.** The judge's
constants get set correctly, the investigator's figure gets re-priced from the
token counts its events already carry, and these labels can go back to saying
billed — with the arithmetic already in place and a test that will notice.

**One number on this page still cannot be fixed by any price list**: the pipe's
priced chains are priced from the same returns, so they carry the error too.
That section already said "priced" rather than "measured", which is the label
the other two just caught up to.
