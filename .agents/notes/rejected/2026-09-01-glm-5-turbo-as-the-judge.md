---
title: glm-5-turbo as the judge — rejected by ledger replay
status: rejected
date: 2026-09-01
scope: hookjudge
---

## Decision

The judge stays on glm-5.3. The cheap-model question the shadow was retired
without answering ("can turbo judge?") is closed by measurement:
`scripts/replay_ledger.py` against 29 rules / 30 days of the production
ledger, majority of three on movers, run twice on 2026-09-01 — once with the
incumbent as its own candidate (the null), once with turbo.

## Why

The null run is the ruler the turbo run is read against:

- **Null (glm-5.3 vs its own ledger):** 7 confirmed differences, 2 new-quiet,
  42% of window traffic, $0.0400. Four of the seven flipped rows were
  recorded AFTER the 2026-08-24 prompt hardening — same prompt, same model,
  days apart — so these are not prompt drift. They are single recorded draws
  sitting on the minority side of the measured 83%/77% floor, reproducibly
  outvoted by a majority of three today. Roughly one rule in four reads
  differently on re-ask; the instrument now states its own error bar.
- **Turbo (glm-5-turbo vs the same ledger):** 16 confirmed differences,
  14 quieter, 7 new-quiet, 69% of window traffic, $0.0347.

The model-attributable difference — turbo's flips minus the null's — is
eleven rules, and the direction is uniform: quieter. The new-quiets land on
exactly the classes with consequence history: the payment-silence rule (zero
orders in an hour — the class the keyword tier once filed low; see the
rule-reuse note), both deliverability-enforcement rules (the provider's
enforcement threshold actually fired once inside this window's ledger), and
an HTTP error-rate rule. A turbo judge would have dropped those cards while
the conditions were live.

The money says nothing, again: $0.0347 against $0.0400 for the whole run, on
a bill the shadow note already measured in cents per week. The saving is
real, microscopic, and priced in silenced payment alerts.

## Consequences

- The judge's model knob stays glm-5.3. Anyone re-opening this re-runs the
  replay (usage in hookjudge/eval/README.md) rather than re-arguing — two
  runs cost seven cents and an afternoon of nobody's time.
- The un-denoisable side is now measured: recorded single draws flip on
  re-ask at roughly the floor rate. The wake-bearing rows this replay
  surfaced — the ones where a flip drops a card — are the labelling queue's
  next afternoon: rows whose wake answer carries consequence deserve labels
  or goldens, not draws.
- hookjudge-b's last stated reason to exist (the live A/B this question was
  waiting on) is void; the retirement proposed in the shadow-conclusion note
  loses its only counterweight. Addendum recorded there.
- Both result files (ids and rule keys only, no alert text) are preserved
  beside the ledger snapshots on the deploy host.
