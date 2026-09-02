---
title: Sample a fraction of quiet-wake-no cards so regret is measured, not assumed
status: proposed
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
