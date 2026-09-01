---
title: Escalation keys on the wrong signal — measured, answered with config, not a wire
status: proposed
date: 2026-09-01
scope: stack
---

## Decision

No judge→probe wire, now or as the default shape. The gap a wire would close
is real but concentrated, so it is answered in order:

1. The rules involved go to the labelling queue first (packet generated
   beside the replay artifacts on the deploy host, five rules, one
   afternoon). The wake axis is the one that matters and the one the old
   rulings lack.
2. Rules a person confirms wake=yes earn explicit per-rule escalation —
   configuration in the deployment's escalation leg (on production that is
   the WebhookWise deep-analysis side; the relay escalation route is
   deliberately absent there — coordinate before touching).
3. Verdict-gated escalation (a pipe route on the judge-notify return, keyed
   on wake) gets a design note only if the long tail appears: judge-only
   signal spread across too many rules for per-rule config to chase.

## Why

Measured on 30 days of the production ledger (2026-09-01), both directions:

- **Waste** (platform ≥ high, judge wake=no): 6 rows / 5 rules. This half
  already has steadier machinery — condition rulings answer re-fires from
  runbooks, the budget breaker backstops — so a wire buys nothing here.
- **Miss** (platform < high, judge said high or wake=yes): 153 rows / 6
  rules, but the wake=yes core is 40 rows in three rules — a
  datasource-blindness rule (20 of 38 at platform low), a
  withdrawal-threshold rule (14 of 37 at medium), and an MQ backlog-growth
  rule (4 of 4 at medium).
- **The axis lesson**: the largest single population (68 rows, importance
  high, wake=yes exactly once) says importance alone must never gate
  escalation — important-and-nothing-to-do is this family's commonest shape.
- **The noise counterweight**: the same day's null replay measured the
  incumbent flipping ~1 rule in 4 on re-ask. A per-event verdict gate
  inherits that noise; platform level is dumber and steady; a human-ruled
  per-rule config is steadier than both.
- **And the twist the packet surfaced**: the two loudest miss-direction
  rules already carry reviewed human rulings BELOW the judge's live answers
  (ruled low and medium; the judge answers high on most firings of both).
  Part of the "miss" is likely the judge over-calling, not the platform
  under-levelling — one more reason the next step is a ruling, not a wire.
  Neither old ruling states a wake expectation; that is the gap the
  adjudication closes.

## Consequences

- Nothing is wired. The deliverables are the five-rule packet (deploy host,
  beside the replay artifacts) and this note. Rulings land in the eval
  dataset, where the deploy gate replays every reviewed row at three votes —
  a config that under-calls a ruling stops shipping.
- Step 2 only ever covers rules a person confirmed; on current evidence the
  MQ backlog-growth rule (4 of 4 wake=yes, no ruling yet) is the cleanest
  candidate and the two loud ones may resolve the other way.
- If step 3 ever fires, its note must answer the noise question head-on
  (wake=yes AND not-first-seen, or a majority draw) — and the judge's return
  leg already rebuilds the original event from the ledger row, so the pipe
  can deliver something investigable; verified in the store's return-leg
  docstring, not yet in practice.
- What would change the answer: labelled wake rulings that CONFIRM the
  judge against the platform on many rules at once. That is the long tail,
  and it re-opens step 3 with evidence in hand.
