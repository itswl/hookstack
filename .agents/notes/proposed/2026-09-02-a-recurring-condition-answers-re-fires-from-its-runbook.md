---
title: A daily-recurring investigated condition should answer re-fires from its runbook, not re-pay
status: proposed
date: 2026-09-02
scope: hookprobe
---

## Decision proposed

For a condition that recurs on a cadence longer than the coalesce window
(the SES bounce alerts fire day after day), stop funding a full cold-start
investigation on every fire once a runbook exists and the last N investigations
reached the same conclusion. Answer the re-fire from the runbook + a cheap delta
check, and earn a real investigation only on a schedule or when the delta moves.

## Why

Measured on production (7 days, 2026-09-02): the investigator spent **$39.6**,
and **$18.6 of it (47%)** went to 20 investigations of three SES bounce-rate
conditions that each returned the same finding. The existing coalescing
(`HOOKPROBE_COALESCE_WINDOW_SECONDS`, default 1800s) joins re-fires of the same
`event_id`/title within 30 minutes into a follow-up turn — but these conditions
re-fire a DAY apart with fresh ids, so each is a new cold start at ~$0.9. The
idempotency key is (source, event_id); a new id is a new run, by design.

The machinery to answer cheaply already exists in the family's vocabulary —
condition rulings, runbooks distilled from prior investigations, the budget
breaker — but nothing routes a recurring, already-understood condition INTO the
cheap path automatically. It waits for a human "not worth it" ruling that, on an
unattended box, never comes ([[2026-08-24-the-worth-column-gets-a-writer-that-says-it-inferred]]
is the same gap from the judge side).

## How (sketch)

- On escalation, before funding a run, check: does a distilled runbook exist for
  this condition AND did the last K investigations of it converge on the same
  conclusion? If so, answer from the runbook (a short "same as last N times,
  here is the standing finding + a one-line current-value check") at ~0 model
  cost, and record it as `route: runbook` in the run ledger so the saving is
  legible.
- Still earn a real investigation on a cadence (e.g. weekly) or when a cheap
  delta signal moves — a ruling nobody re-checks is a prejudice with a
  timestamp, which this family already says out loud.
- This is the investigator-side twin of the judge's reuse route: one paid answer
  amortised across restatements, with a scheduled re-verification.

## Consequences

- Biggest single spend line drops without a model change; the cost curve bends
  the way the README promises (dollars per answered incident falling).
- The risk is answering "same as last time" when this time is different — hence
  the delta check and the scheduled real run. The delta signal is the hard part
  and should be conservative (when unsure, pay for the investigation).
- Simpler interim, no code: rule the three SES conditions "not worth a fresh
  investigation" by hand once, and let the existing ruling path answer re-fires.
  That is the operational stopgap while this is built.
