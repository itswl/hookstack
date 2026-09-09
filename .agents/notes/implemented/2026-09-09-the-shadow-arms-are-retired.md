---
title: Both comparison arms retired — one was measuring itself, the other was a subscription nobody read
status: implemented
date: 2026-09-09
scope: stack
---

## Decision

`hookjudge-b` and `hookjudge-c` are gone from `deploy/docker-compose.shadow.yml`
and from the `shadow-to-judges` fan-out. One brain answers now. Their ledgers
stay on disk at `../shadow-data/hookjudge-{b,c}`.

The operator decided it on cost. This note exists because the decision reverses
`implemented/2026-09-03-a-third-brain-and-a-guard-against-flattening.md`, which
is six days old, and a reversal that fast deserves the numbers that caused it
rather than a shrug.

## Why

Read from the arms' own status endpoints, all three having judged the same 53
events with an identical route split (ai 31, recovery 17, reuse 4, rule 1):

| | model | ai cost | latency | verdicts returned |
| --- | --- | ---: | ---: | --- |
| `hookjudge` | gpt-5.6-luna | $0.0146 | 4,619 ms | 53 sent |
| `hookjudge-b` | gpt-5.6-luna | $0.0127 | 4,615 ms | **53 skipped** |
| `hookjudge-c` | gemini-2.5-pro | **$0.3753** | **12,498 ms** | **53 skipped** |

**`hookjudge-b` was still comparing a model with itself.** That is not a new
finding — `rejected/2026-08-24-conclude-the-shadow-it-compared-a-model-with-itself.md`
measured it across 386 events and proposed retiring b for exactly this reason.
The rejection that followed did not fix b; it added c. So b spent another two
weeks running the live judge's model beside the live judge, differing only in
its price constants.

**`hookjudge-c` was the real instrument and it worked.** It is the only arm that
thought differently: it UPGRADED severity where the live judge downgrades
(`high → critical` 3, `low → medium` 6, against A and B which only ever
downgrade), and it would have woken a person 9 times in 53 against the live
judge's 7. The third-brain note's premise was sound.

What ended it is that **nothing read the verdicts**. `HOOKJUDGE_RETURN_URL=none`
on both arms, `returns: {skipped: 53}` in their own ledgers, and the
disagreement matrix that was supposed to become labelled data has produced
`ruled: 0` — no person has ruled anything, on any arm. The 09-03 note argued the
third brain's marginal cost was "a third of cents". Measured, it is 25.7x the
live judge, and it is buying a second opinion nobody reads.

**A second opinion nobody reads is a subscription, not an instrument.**

## The finding b leaves behind, which is worth more than b was

A and B ran the same model on the same 31 calls and reported costs 15% apart,
because their per-1k price constants differ:

```
hookjudge    in 0.0002   out 0.0012
hookjudge-b  in 0.00028  out 0.00042
```

Identical traffic cannot cost two different amounts. One of those price sheets
is wrong, and the live judge's is the one feeding `scripts/cost_report.py`, so
the weekly bill is wrong by up to 15% in a direction nobody has established.
**That is now an open question with no instrument attached to it**, and settling
it needs the gateway's real price list, not a second container.

## Consequences

**The weekly report gets more honest by accident.** `cost_report.py` reads the
judge on port 8200 — the live one — so the arms' spend never appeared in it. The
bill it printed was already the bill for one judge; now that is also the bill for
the deployment.

**Restoring an arm is three things**: a service block, a channel in
`shadow.yaml`, and one name in the `shadow-to-judges` fan-out. Both compose and
the config carry a comment saying so, because the next person to want a second
opinion should find the reasoning rather than the gap.

**The disagreement question is not answered, only unfunded.** If cross-vendor
labelling is wanted again, the thing to fix first is that nobody rules: an arm
whose ledger is never read cannot pay for itself at any price.
