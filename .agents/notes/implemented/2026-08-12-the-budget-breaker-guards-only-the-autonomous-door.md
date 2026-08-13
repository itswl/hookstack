---
title: The budget breaker guards only the door that spends without being asked
status: implemented
date: 2026-08-12
scope: hookprobe
---

## Decision

`HOOKPROBE_BUDGET_USD` over `HOOKPROBE_BUDGET_WINDOW_HOURS` gates exactly one path:
the escalation door at `POST /hooks/event`, where the pipe's traffic buys
investigations with nobody asking. Operator-driven doors — `/hooks/agent`, session
continuation, the console — are never gated.

A refusal is not a dropped request. It settles as a report-shaped run that returns
through the family loop with a card saying the window's spend hit the ceiling, and
`GET /v1/budget` shows the arithmetic behind it.

## Why

Cost risk here is not "an expensive model": it is an alert storm turning into N paid
investigations while everyone is asleep. That risk lives entirely on the autonomous
door. Gating an operator who is sitting there asking a question adds no safety and
takes away the tool at the moment it is wanted.

Refusals report themselves because a silent refusal is indistinguishable from a
broken investigator. The loop's promise is that every escalation ends in a card,
and a refusal is an outcome, not an exception to that.

The ledger counts recorded turns only, so in-flight spend is missing and the figure
trails reality by at most `HOOKPROBE_MAX_CONCURRENT` runs. That is deliberate: this
is a brake, not an invoice.

## Consequences

- Cost overruns are bounded per window, not per alert. Idempotency bounds the
  redelivery of one event; a genuinely new event id funds its own investigation, and
  the breaker is what bounds that.
- An operator can still spend freely, on purpose. Nothing stops a human from running
  the window's budget out through the console.
- The reported spend prices the model the CLI thinks it is calling. On a non-Anthropic
  endpoint reached through the Anthropic dialect it is an over-estimate, so treat the
  number as a brake reading, not billing.
