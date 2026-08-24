---
title: Act on the verdicts already paid for — wake routes delivery, rulings gate spend
status: implemented
date: 2026-08-24
scope: stack
---

## Decision

Three changes, one arc: the system computed two judgements per condition — "does
a person need to act now" (wake_someone) and "was investigating this worth it"
(ai_rulings) — and nothing consumed either. A week of production data made the
cost of that concrete: 440 interruptions, 95% repeats, 280 cards delivered to
one person's chat, and one condition ruled not_worth_it twice then investigated
ten more times at full price.

1. **wake_someone travels and quiets.** It rides reuse verdicts the same way
   importance does (117 of one rule's 194 rows had a blank answer because only
   the AI-routed firing carried one), rides the return payload to the pipe, and
   deploy/shadow.yaml drops an explicit `wake: "no"` with a named skip_code
   before routing. '' fails open into a card — asserted by
   assert_shadow_config, which also refuses a match loosened to a list.

2. **A standing not_worth_it ruling answers repeats from the runbook.** The
   probe keeps its own append-only copy of every ruling it files
   (rulings.jsonl — acting on a verdict must not require read access to a
   sibling's ledger). A re-fire of a ruled-useless condition with a
   consolidated runbook gets a report-shaped JSON answer at $0. Every clause
   that holds the gate open is tested: force, worth_it, stale TTL, no runbook,
   and no recent REAL run — gated answers do not count as verification, or the
   gate would feed itself forever.

3. **Agent-door runs learn their condition.** The platform door takes a
   finished prompt, so its runs had no meta: the board showed thirty rows of
   identical instruction boilerplate, and no gate could tell which CONDITION a
   run was about. The prompts embed the alert as JSON; the service reads
   rule_name/source/level back out, charset-constrained so the prompt's own
   prose descriptions cannot match, and marks the meta as derived.

## Why the boundaries sit where they do

The wake quiet is DELIVERY policy, not data: every quieted verdict is still in
both ledgers and on the boards. The judge did not gain routing opinions; the
pipe did not gain judging opinions; the filter stage is config, reversible by
deleting four lines of YAML.

The ruling gate consumes only what this service itself produced. The judge's
copy stays authoritative for display; the probe acts on its own memory of what
it filed, seeded once on deploy from the judge's table and refreshed by every
future patrol. No new token, no new cross-service read.

not_worth_it gates the PROBE's spend, wake_no gates the HUMAN's attention, and
they stay separate axes: the two payment alarms are worth_it (real transactions,
occasionally anomalous ratios) yet consistently wake:no — suppressing either
judgement with the other would have been wrong in both directions.

## Consequences

Expected, measurable next week: cards/week drops from ~280 toward the wake_yes
volume plus unanswered; probe spend drops by whatever DatasourceNoData-class
conditions were costing (~$5-8/wk of ~$50 at the time of writing); the runs
board becomes scannable by condition name. The wake filter's skip_code makes
the quieting itself countable — `wake_no` rows on the relay ledger are the
number to read.

New failure surface, named: a wrong not_worth_it ruling now has teeth for up to
ruling_ttl_days. Before, it was a wrong number on a board; now it answers real
alerts with a runbook until the TTL lapses, the weekly patrol declines to
refile it, or a reverify run flips it. That trade was the point — but it is a
trade, and the reverify clause is its price.

## What would change the answer

- A wake:no verdict that a person later rules mattered. The boards and the
  weekly patrol are the audit for that; if it happens, the quiet stage narrows
  (per-rule) or dies.
- WebhookWise failing to parse a report-shaped JSON answer where it expected
  its own schema. Its ingestion already tolerates imperfect model output, and
  the answer carries `summary` at the top for the pipe's own renderer, but the
  first gated run on production deserves a look from their side.
- The reverify run repeatedly re-earning the same not_worth_it: that is the
  gate working. The reverify run repeatedly flipping the verdict: that is the
  TTL doing its job badly, and the number to tune is days, not the design.
