---
title: Six runbooks for one fact — knowledge is keyed by alert title, not by cause
status: proposed
date: 2026-08-21
scope: hookprobe
---

## Decision

Not built, and not obviously buildable yet. Recording it because the export
feature made the shape visible for the first time and it will not get less true.

## What the library actually looks like

`GET /v1/skills/export` listed eleven runbooks. Six of them are SES:

    ses-all-type-bounce-volume-high-24h-delivery
    ses-bounce-rate-10-aws-pauses-sending
    ses-bounce-rate-5-aws-review-threshold
    ses-bounce-rate-5-aws-review-threshold-44233
    ses-bounce-rate-5-for-3-days-running
    ses-bounce-spike-200-in-1h
    ses-reputation-bounce-rate-5-account-suspension

Seven, counting the near-duplicate pair. The Friday self-review had already said
so in prose, from the other side:

> **SES bounce cluster** (8 of the last 20, across 5 rule names): all describe
> one underlying fact — the SES account's reputation bounce rate at ~10.3%.

One fact. Seven runbooks, each with one or two cases, none of them consolidated,
each paying for its own investigation.

## Why

`auto_write` names a runbook with `slug(_headline(turns, current_message))`, and
the headline is the alert title. So the key is **the monitoring rule that
fired**, while the knowledge is about **the thing that is wrong**. One misbehaving
SES account crossing five thresholds produces five rule names and therefore five
runbooks, and the sixth investigation learns nothing from the first five because
it loads a different file.

`consolidate_at` merges cases WITHIN one runbook. Nothing merges across.

### Why it is not a small fix

The obvious answer — cluster related runbooks and merge them — needs a notion of
"related" that this service does not have and should not guess at. Title
similarity would have merged `ses-bounce-rate-5-aws-review-threshold` with
`ses-bounce-rate-10-aws-pauses-sending`, which are genuinely different
thresholds with different consequences (review versus suspension), while missing
that `[SES] All-type bounce volume high` is the same account.

Three candidate signals exist and none is ready:

* **hookjudge `identity`** already groups a firing with its recovery, but it is
  built from source + title, so it splits exactly where the runbooks split.
* **`burst_id`** groups alerts by origin within a window — closer to "one
  underlying fault", and only ever looks one window back.
* **The investigator's own conclusions.** Five SES case files all say the same
  sentence about reputation. A consolidation run reading ACROSS runbooks could
  see that, and that is a model call over a corpus rather than a similarity
  function — which is the sort of thing this service is for.

The third is the interesting one and it is a real feature, not a tweak.

## Consequences

Bounded and visible. Each duplicate costs one investigation that could have been
a runbook hit, at roughly a dollar. The export names every unconsolidated runbook
with its case count, so the pile is legible rather than silent, and the weekly
self-review reports it in prose. Nothing degrades; the library just stays wider
and shallower than it should be.

### What would change the answer

A runbook reaching `consolidate_at` and STILL leaving its siblings untouched —
i.e. the first time consolidation runs on one SES runbook and the operator can
see it ignoring six neighbours saying the same thing. At that point there is a
concrete before-and-after to design against, which there is not today.
