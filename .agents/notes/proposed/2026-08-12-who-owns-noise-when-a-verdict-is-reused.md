---
title: Reuse saves money, not attention — pick an owner for noise before closing it
status: proposed
date: 2026-08-12
scope: stack
---

## Decision

Leave it open, deliberately, and keep both closures on record. The paired posture
turns the pipe's deduplication **off** on the grounds that the brain owns noise
accounting; the brain currently accounts only for spend. A storm of N restatements
therefore costs one verdict and still sends N identical cards.

Decided 2026-08-12: keep it that way until repeated cards actually hurt. Money was
the problem worth solving first.

## Why

`reuse` is a cost route, not a suppression route: the second identical alert is
answered from the first verdict at no model cost, which is exactly what it was built
for. Nobody asked for fewer cards, and a suppression mechanism that hides a genuine
escalation is worse than a duplicate card that annoys.

The two ways to close it, with the trade-off that keeps each one from being obvious:

1. **The brain returns a suppression signal on `reuse` and the pipe drops the
   delivery.** One place decides what is noise — but a suppressed card can hide a
   real escalation, and the pipe's ledger stops describing what a human saw.
2. **The pipe deduplicates its return door.** Simpler and keeps the brain
   content-blind — but now two components decide what is noise, and the brain's
   ledger no longer describes what was delivered.

## Consequences

- Nothing to build today. The decision is that the gap is known and priced, not that
  it is fixed.
- The trigger to revisit is evidence, not taste: repeated cards from one condition
  drawing a complaint, or an operator muting a channel.
- Whichever closure wins, it changes who owns "is this worth telling a human", which
  is an architectural boundary — hence a note rather than a backlog line.
- Recorded alongside this in `hookjudge/examples/with-hookrelay/README.md` and
  STACK.md's known gaps, where an operator reading the configuration will meet it.
