---
title: An outside provider's enforcement line crossed is high before the provider acts, and the prompt says so
status: proposed
date: 2026-09-14
scope: hookjudge
---

## Decision

Proposed, tried once, and pulled back the same afternoon — the prompt in
production is still `hookjudge-judge-v1`. The proposal: the `high` anchor gains
one clause: an outside provider's enforcement line crossed — an account-level
bounce, complaint or abuse threshold past which the provider may suspend the
account or pause sending — is high even while the provider has not acted yet,
because it will not fix itself and every user of that channel fails the moment
it does. Nothing else in the prompt moves. `eval/scenarios.jsonl` gets re-bound to the
new version and hash; the safety scenarios are unaffected (they pin the rule
route).

**Why it was pulled back.** The draft shipped as `v2` in e4df9bf and the deploy
gate replayed the enlarged golden set against it, twice. The provider-pause row
was held out and so not tested; what the replays did show was a row moving the
wrong way: a production observability blackout (three core services sending no
logs for thirty minutes) answered medium three votes of three, where the same
row had held a high majority in every replay under v1. One clause about
provider lines should not lower a blackout, and one three-vote sample cannot
say whether it did or the coin did — which is the point: a prompt change ships
on an A/B replay at five votes over the whole set, not on one red gate. The
text was restored to v1 in the next commit; nothing reached production.

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

- To ship this: replay the whole host set at five votes under v1 and under the
  draft, on the same day, and compare per row — the pause row (held) must move
  to high and the blackout row must not move at all. `replay_ledger.py` is the
  shape of that comparison; the golden set is the corpus.
- Until then the held golden row (the pause instance) stays documented, not
  gating, and the mail family's account-line rules keep the v1 answers — mostly
  high in production, medium on a coin.
- Watch `over_escalated` on the replay either way: 9 of 36 firing rows under v1.
