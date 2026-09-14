---
title: An outside provider's enforcement line crossed is high before the provider acts, and the prompt says so
status: implemented
date: 2026-09-14
scope: hookjudge
---

## Decision

The judge's system prompt is `hookjudge-judge-v2`. The `high` anchor gains one
clause: an outside provider's enforcement line crossed — an account-level
bounce, complaint or abuse threshold past which the provider may suspend the
account or pause sending — is high even while the provider has not acted yet,
because it will not fix itself and every user of that channel fails the moment
it does. Nothing else in the prompt moved. `eval/scenarios.jsonl` is re-bound to
the new version and hash, and the safety scenarios hold unchanged.

## Why

The first replay of the enlarged golden set answered the mail family's
provider-pause instance — a bounce rate past the provider's pause line, the
alert itself saying all platform mail may stop — with `medium, high, medium`.
The prompt's own scale puts that at high ("broken or breaching and it will not
fix itself"), but the medium anchor's example, "a threshold crossed with days of
headroom", is the sentence a threshold alert resembles at a glance, and two
draws of three read it that way. The production ledger shows the same rule high
on every paid verdict for thirty days, which is what a coin looks like when
nobody replays it; the gate replayed it, and that is the gate doing the one job
it has.

The clause is written narrowly on purpose. It names the provider's line, not
ours: a capacity threshold with days of headroom stays medium, a security-update
notice from the same provider stays low, and both have golden rows of their own
to hold them there.

## Consequences

- Judged importance on the mail family's account-line rules becomes consistent
  where it was already mostly high; the escalation leg does not see it, because
  that family is declined at the investigator's door since this morning.
- The held golden row (the pause instance) flips to reviewed once this prompt
  passes it twice at three votes; until then it is documented, not gating.
- Watch `over_escalated` on the replay: 9 of 36 before this change. A rise on
  rows that are not enforcement lines would mean the clause reads wider than it
  is written.
