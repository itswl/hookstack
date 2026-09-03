---
title: Conclude the shadow — it spent 386 samples comparing a model with itself
status: rejected
date: 2026-08-24
scope: stack
---

> **Rejected 2026-09-03** — see
> `implemented/2026-09-03-a-third-brain-and-a-guard-against-flattening.md`.
> The measurements below stand and are why the guard exists; the conclusion
> does not. The premise this note found unasserted is now asserted, and the
> disagreement-mining it concedes as the shadow's unique value got better
> with a third vendor rather than being retired.

## Decision proposed

Retire hookjudge-b. Keep its ledger (the backups already do), keep the one
real finding it produced, and let the golden replay gate be the instrument the
shadow was reaching for.

## What the ledgers actually say

413 events judged by both brains, joined on correlation_id, segmented by what
each side was RUNNING:

- **386 of 413: deepseek-v4-flash on BOTH sides.** For most of its life the
  experiment compared a model with itself. Importance self-agreement 83%
  (B over-called 60, under-called 7 — mostly the 充值 medium/high split),
  wake self-agreement 77%. Nobody noticed, because nothing asserted the two
  sides were configured to differ.
- **14 of 413: glm-5.3 vs glm-5-turbo — the intended A/B began today.**
  86% importance agreement, turbo under-called twice (both DatasourceNoData),
  turbo costs half. Far too thin to conclude anything.
- **5 human-ruled rows:** B disagreed on two, over-calling where the person
  said the interruption did not matter. Directionally favours A; five presses
  is not a trend.

## The accidental finding, which is worth keeping

Same model, same prompt, same alerts: 83% importance / 77% wake agreement
WITH ITSELF. That is the judge's noise floor, measured on 386 production
events — and it independently justifies the eval gate's 3-vote majority
design, which was adopted the same day from the other direction (a golden
flipping red/green on identical input). Any future "the judges disagree"
number smaller than ~20 points is indistinguishable from this floor.

## Why conclude rather than run the real A/B now

The money question ("can the cheap model judge?") has almost no money on it:
the judge's whole AI bill is cents per week, and turbo halves cents. The
quality question is now answered better by the golden replay — labelled
expectations against the shipping prompt, gating deploys — than by unlabelled
agreement between two models, which cannot say who is RIGHT (the shadow's own
config comments concede this). What the shadow uniquely offers — mining
A/B disagreements as golden-set candidates — works offline against the
retained ledgers and does not need a live second brain.

Cost of keeping it: a container, its rebuilds on every deploy, double ingest
on every event, and an assert_shadow_config that requires two brains —
machinery whose question has dissolved.

## What retiring takes (the parts that will object)

- deploy/shadow.yaml: drop to-judge-b from channels and the fan-out route.
- deploy/docker-compose.shadow.yml: remove the service.
- scripts/assert_shadow_config.py REQUIRES >= 2 brain channels ("a shadow run
  with one brain is not comparing anything") — true, and the answer is that
  this stops being a shadow run: the check's posture rules stay, the two-brain
  minimum goes, and the file says why.
- The B ledger stays on the volume and in the nightly snapshots.

## Consequences

If adopted: one container gone, deploys stop rebuilding it, every event stops
being ingested twice, and the noise-floor finding (83%/77% self-agreement)
moves into the record here rather than continuing to be re-measured. The
disagreement-mining option survives against the retained ledgers. If the
question ever comes back, the fan-out pattern is one route and one channel
away — retiring the container does not burn the design.

If declined: the run must gain the assertion its first era lacked — the two
sides MUST be configured to differ, or the shadow refuses to start — because
386 samples of a model agreeing with itself is the failure mode this note
exists to record.

## Addendum, same day: the disagreement axis measured its own instrument

The cost above shrank within hours of being written. Re-sampling the queue's
judges at three votes each showed 4 of the 5 "contested" rows were one judge
flipping against itself (per-judge flip rate 11/32; 59% of rows had at least
one flip — the pairwise form of the 83%/77% floor this note records). Denoised,
the disagreement axis located nothing on this corpus, and the queue now
disqualifies its own ranking above 20% instability. So the third opinion B
supplies feeds an axis that, on current evidence, ranks noise. The retirement
cost stands as written — it is just smaller than it looked for one afternoon,
and the requirement survives either way: if B goes, the queue must SAY it has
two sources, not quietly rank on less.

## What would change the answer

Wanting the 5.3-vs-turbo answer anyway: then set a decision date (2-4 weeks),
add the config assertion the first era lacked (the two sides MUST differ, or
the run refuses), and let it accumulate — the fan-out is already wired and
the marginal cost is cents. Proposed against because the eval gate already
guards the quality axis with labels, which agreement never had.

A cost the retirement carries, added from the labelling side (2026-08-24): the
B judge is currently the THIRD independent opinion in
`hookjudge/scripts/eval_queue.py`, which orders the unlabelled corpus by where
independent judgements disagree. Retire it and the queue keeps two sources — the
free rule route and one model — and loses the signal it was built on. Rule route
versus model is not two opinions about the same question; those two disagree
almost by construction, so nearly every row would read "contested" and the
ordering would stop discriminating.

The available substitute is raising `--votes` and using same-model
self-consistency as the second axis. It is not equivalent, and the difference is
exactly the one this note is about: self-consistency measures whether the
INSTRUMENT is steady, not whether two different judgements about the alert
diverge. Measured this afternoon, that instrument is 77-83% steady, which is why
the queue now separates "judges stably disagree" from "one judge flipped" — the
second is a measurement problem and belongs in a different queue.

So the retirement is defensible and the cost is real: it trades a diversity axis
for a stability axis. If it goes ahead, the labelling queue should say in its own
output that it is down to two sources, rather than quietly ranking on less.

**Read the addendum below before weighing this paragraph.** It was written from
the one-sample picture, where five rows looked contested and a third opinion was
doing visible work. Re-sampling narrowed that the same afternoon, and the
argument above — that losing B would leave an ordering unable to discriminate —
assumes an ordering that discriminates today. On current evidence it does not.

## Addendum (2026-09-01): the offline form answered the question

The instrument this note predicted — "mining offline against the retained
ledgers, no live second brain" — was built on 2026-08-31 as
`hookjudge/scripts/replay_ledger.py`, and on 2026-09-01 it answered the
5.3-vs-turbo question this shadow was kept alive for: against 29 rules and
30 days of ledger, turbo confirmed 16 differences to the null run's 7,
fourteen of them quieter, seven of them new-quiets landing on payment-silence
and deliverability-enforcement classes. Seven cents, both runs together.
The ruling is recorded in
`rejected/2026-09-01-glm-5-turbo-as-the-judge.md`: the judge stays on 5.3.

What this changes here: the one thing retirement was said to cost — the live
A/B whose question might someday be wanted — has been answered without a
live second brain, at a price that makes re-asking routine. The proposal
above now stands with no counterweight.
