---
title: Sample a fraction of quiet-wake-no cards so regret is measured, not assumed
status: implemented
date: 2026-09-02
scope: stack
---

## Decision proposed

Add an opt-in, deterministic sample to the `quiet-wake-no` drop: a small
fraction of the conditions the judge rules "no wake" are delivered anyway,
labelled a silent-audit sample, so a person can occasionally rule on what the
quiet swallowed — turning the regret counter from a hope into a measurement.
Default OFF; nothing changes until an operator sets the rate.

## Why

Measured on production (2026-09-02): over 168h the pipe dropped **159 of 248**
judge-notify cards on `wake_no` (64%), and the human-ruling column is 0 — nobody
has ever pressed a card, so `quiet_regrets` is 0 because there is no signal, not
because nothing was mis-quieted. The whole attention loop rests on a number that
cannot move. Meanwhile two rules (充值/提现 thresholds) answer wake=no on nearly
every fire and dominate the drop, and their AI rulings call them "worth it"
because the judge is answering "is this real" not "must someone act now".

A sample makes the silence auditable: deliver, say, 1-in-20 of the wake=no cards
(deterministic by identity, so the SAME condition is consistently sampled rather
than flickering), clearly marked "silent-audit — you were not going to be paged
for this", and let the existing card ruling feed the regret counter. Over weeks
that yields an actual mis-quiet rate per rule, which is what tells you whether
the 64% is 64% of noise or is burying something.

## How (sketch)

- The drop is a config stage (`deploy/shadow.yaml` `quiet-wake-no`,
  `when: {source: judge-notify, wake: "no"}`). The processor engine has no
  sampling primitive today, so this needs a small capability: a `sample_pct`
  (and a stable hash of the event identity, not a coin flip) that PASSES the
  matched fraction instead of dropping, tagging the delivery
  `skip_code: wake_no_sampled` so the ledger still separates it from a real
  wake=yes.
- The card carries a one-line banner naming it a sample, so a person reading it
  knows the system is NOT claiming they needed it.
- The judge's attention block already counts interruptions/rulings; a sampled
  card that a person marks "actually mattered" is exactly a `quiet_regret`.

## Consequences

- It spends a little attention to measure attention — which is why it is opt-in
  and small. On a channel nobody can press (no interactive callbacks), it buys
  nothing and should stay off; here the lark-bridge makes presses receivable, so
  it can work.
- Deterministic-by-identity matters: a random per-fire sample would deliver a
  storm's Nth restatement and look like the dedup broke.
- Rejected alternative: deliver ALL wake=no as "FYI, muted". That is just
  turning the quiet off, which the whole design is against — the point is a
  measured fraction, not a firehose.

## Built (2026-09-06)

The `filter` processor gained `sample_pct` (default 0, off), `sample_by`
(default `title`) and `sample_banner`. When a filter matches, a deterministic
fraction of the CONDITIONS — sha256 of the sample key, so it survives a restart
and one condition is always sampled or never — PASSES instead of dropping. The
sampled event carries the banner prepended to its body and `fields.audit_sample`
holding the code it dodged, so the ledger separates it from a real delivery.

The whole change is in hookrelay. The judge side needed NOTHING: a sampled
wake=no card is a wake=no row that is now deliverable, so a press of "actually
mattered" flows through the existing `/feedback` → `record_mattered` →
`quiet_regrets` counter that was already there and reading 0 for want of a
signal. That is the shape the note predicted — "the judge's attention block
already counts a sampled card a person marks mattered as exactly a
quiet_regret".

Wired into shadow.yaml at `${QUIET_SAMPLE_PCT}`, default 0 through the compose
file. Off until an operator sets a rate, and the note's own caveat stands: worth
nothing on a channel nobody can press, so it stays off where the lark-bridge is
absent (the work deployment does not sample at all).

This is the symmetric other half of the automation-graduation record shipped the
same day: that one measures whether an auto-APPLIED action was wrong; this one
measures whether a SILENCED one should not have been. Both turn a regret counter
from a hope into a measurement, and both are sampling reviews rather than
firehoses — a measured fraction, looked at after the fact, because nobody here
answers in real time.
