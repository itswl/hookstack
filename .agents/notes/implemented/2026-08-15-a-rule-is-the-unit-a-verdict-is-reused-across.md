---
title: A rule, not an identity, is the unit a paid verdict is reused across
status: implemented
date: 2026-08-15
scope: hookjudge
---

## Decision

Add one route between reuse and the model: `rule-reuse`, which answers with this
alert **rule's** own last `ai` verdict. Off by default
(`HOOKJUDGE_RULE_REUSE_WINDOW_SECONDS=0`), and it refuses three cases — a
non-`ai` prior, a different level, and re-serving the prior summary.

## Why

The obvious first tier is a cheap model or a keyword pre-filter deciding the
easy 80%. Measured against real traffic, that is the wrong tier and a dangerous
one.

795 alerts from one production Grafana, grouped by `alertname`:

| grouping | rows |
| --- | --- |
| raw alerts | 795 |
| distinct identities | 601 |
| distinct rules | 29 |

Three rules are 80% of the volume. And within the `ai` route, **28 of the 29
rules produced exactly one verdict across every firing** — 295 alerts, one
answer each. The second firing of a rule is a question already paid for, so the
saving is available without asking anything cheaper or dumber.

The keyword tier, meanwhile, was already running next door and doing harm. In
WebhookWise the same two payment rules show:

```
high | redis_reuse | 269      high | ai         | 257
low  | rule_routed |  73      low  | redis_reuse |   3
```

Every `low` came from the rule route. The model called all 257 of its own
`high`. So a cost tier built on keywords filed 76 payment alerts as low —
exactly the silent downgrade the eval harness counts as `missed`, in production,
before anyone proposed adding one here.

That is also why only `ai` priors are reusable: hookjudge already refused to
reuse rule verdicts for storms, and the same reasoning extends to rules. The
three refusals are each a way this could hide something rather than save
something.

## Consequences

- The saving is bounded by how long a window is open and how many rules a
  deployment has. It is not free: a rule whose severity genuinely depends on the
  firing (an amount crossing a bigger threshold) will be answered from the rule's
  last verdict until the window expires. The one rule of 29 that varied is the
  shape to watch.
- `judgements` gains `rule_key` and `level`, with a `PRAGMA table_info`
  migration — `CREATE TABLE IF NOT EXISTS` does nothing to a ledger that already
  exists, so the first INSERT naming a new column would have failed on every
  running deployment.
- Turning it on is a measurement, not a config change: run
  [the eval](../../../hookjudge/eval/README.md) on both settings and compare
  `missed` before `cost_total`.
- The finding about WebhookWise's rule route belongs to WebhookWise; it is
  recorded here because it is the evidence this design rests on.
