---
title: An outside provider's enforcement line crossed is high before the provider acts, and the prompt says so
status: rejected
date: 2026-09-14
scope: hookjudge
---

## Decision

Rejected on the A/B replay it asked for, the same afternoon; the prompt in
production is `hookjudge-judge-v1` and stays so. The proposal was: the `high`
anchor gains one clause: an outside provider's enforcement line crossed — an account-level
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

**The A/B, 2026-09-14 14:44–14:49 UTC.** All sixty host rows, five votes each,
same image, the candidate prompt mounted over the module. Under v1 the
provider-pause row answered high five times out of five — the two-of-three
medium that started this was the coin, not the prompt. Under the draft, six
rows moved DOWN one or two levels (a datasource-no-data instance, a daily-P&L
instance, a probe, a marked test, the observability blackout, a test-broker CPU
row) and none moved up; three wake answers changed; unstable rows went from 17
to 20. A clause written about one provider line made the whole scale more
conservative. That is the opposite of its purpose and a change nobody asked
for, so it is rejected rather than left proposed.

## Consequences

- The pause row is reviewed again and gating; the gate now runs at five votes,
  which is the lesson the replay actually taught — see the labelling note.
- The opinion files are kept beside the labelling backups on the host. Anyone
  reopening this starts from them, not from a fresh draft.
- The mail family's account-line rules keep the v1 answers, which at five votes
  are high.
