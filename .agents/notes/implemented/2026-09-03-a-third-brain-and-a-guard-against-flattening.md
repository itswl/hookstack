---
title: A third brain, and the assertion that keeps the three of them different
status: implemented
date: 2026-09-03
scope: stack
supersedes: 2026-08-24-conclude-the-shadow-it-compared-a-model-with-itself
---

## Decision

The shadow keeps running and gains a third brain. `hookjudge-c` joins A and B
on the same fan-out (`shadow-to-judges` now sends to three channels), ledger-only
like B, on its own port and its own data directory.

`assert_shadow_config.py` gains the assertion the experiment has never had: the
brains must be configured to DIFFER. It resolves each judge service's
`HOOKJUDGE_AI_MODEL` the way compose does — env file first, compose default
second — and fails on two things:

- two or more brains resolving to the same model;
- any brain taking its model from a compose DEFAULT rather than the env file,
  because those defaults still name DeepSeek and a half-finished provider swap
  would silently point a brain at a vendor nobody chose.

A brain with `${VAR:?}` and nothing set is reported, not failed: compose already
refuses to start. Where there is no `.env` beside the compose file — inside the
container the smoke runs it in — the check is skipped and the summary line SAYS
it was skipped.

## Why

The superseded note is right about what it measured and wrong about what to do
next, and both halves matter.

It is right that 386 of 413 events compared a model with itself, that the
resulting 83% importance / 77% wake agreement is a noise floor rather than a
finding, and that unlabelled agreement between two models cannot say which one
is correct. It happened a second time on 2026-09-03: a provider swap pointed
brain B at brain A's model, both containers stayed healthy, both ledgers kept
filling, and nothing complained. Twice is a pattern, and the pattern is that
nothing was ASSERTING the premise the whole experiment rests on.

Where it goes wrong is the conclusion. Retiring the shadow removes the machinery
because the machinery was misconfigured — and the misconfiguration is cheap to
make impossible, which is what the assertion above does. Meanwhile the thing the
note concedes the shadow uniquely offers, mining disagreement for golden-set
candidates, gets materially better with three brains from three vendors than it
ever was with two: a three-way split is a much stronger signal that an alert is
genuinely ambiguous than a two-way one, and the eval set is the current
bottleneck on every quality question in this family.

The cost objection also shrank. The whole judge AI bill is cents per week; the
third brain's marginal cost is a third of cents.

## Consequences

Three brains means three times the ingest per event and a third container
rebuilt on every deploy — accepted, and still cheap at this traffic (~2 alerts
an hour).

The guard reads `<repo>/.env`, so it is honest about a laptop too: a dev
checkout that configures only brain A now fails the check, correctly, because
running the shadow from that checkout would give brain B a DeepSeek default.
That is noise for anyone who does not deploy the shadow, and the summary line
names the reason so it is at least self-explaining.

`assert_shadow_config` still requires >= 2 brain channels, so the retirement
path the superseded note describes remains available: it needs that floor
lowered, and now also this assertion removed.

Brain B was still on brain A's model when this shipped, waiting on a working
BigModel credential — so the guard fails against production until B moves, which
is the guard doing its job rather than a defect in it.
