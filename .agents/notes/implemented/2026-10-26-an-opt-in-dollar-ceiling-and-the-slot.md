---
title: An opt-in dollar ceiling for the judge, and a queue that re-checks it at the slot
status: implemented
date: 2026-10-26
scope: stack
---

## Decision

1. **The judge has an OFF-by-default budget.** `HOOKJUDGE_BUDGET_USD` (default
   0) and `HOOKJUDGE_BUDGET_WINDOW_HOURS` (default 24) cap what the judge itself
   may spend. Past the ceiling, an event that would need a paid call is judged
   by the keyword floor and the row says `judge budget exhausted: $x of $y in
   Nh`. It is still a verdict — a cost brake must not become a missing alert.
   The spend is summed from the judge's own rows (`Store.spent_since`), which is
   a floor rather than an invoice, for the same reason the investigator's window
   is: a call that died before a response left no cost.
2. **The investigator re-checks its budget at the slot.** The event door checks
   the meter before a run queues; a burst can put the process over between that
   check and the moment that actually spends. `RunService._execute` now checks
   `budget_state()` after acquiring the semaphore and settles the run as failed
   with `budget exhausted while queued` before the engine is called.
3. **The judge ceiling moves to 3,600** for the budget branch and its tests; the
   raise is argued in `scripts/assert_weight.py` and stated in the README.

## Why

The review found the cost story had no hard ceiling in the judge and only a
door-level ceiling in the investigator. Both are opt-in: default 0 preserves the
behaviour every existing deployment already has. The failure the ceilings are
for is a burst or a runbook loop spending unattended, not a single judgement;
pricing that as "a verdict is missing" would be worse than the bill.

## Consequences

- A judge deployment that sets a ceiling can plan a dollar figure per day, and
  a human reading `/status` sees the degraded rows with the reason. A deployment
  that does not set one is unchanged.
- The investigator's queued run now fails with a report instead of running past
  the ceiling. That report says why, so it is an interruption with a cause, not
  a silent stop.
- The two ceilings do not share a ledger. The judge measures its own paid
  calls; the investigator measures its own turns. A single dollar figure across
  both is a cross-service account nobody has written down, and inventing one
  here would make each service's number disagree with the other's.
