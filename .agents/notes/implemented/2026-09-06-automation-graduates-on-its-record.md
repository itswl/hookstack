---
title: Automation graduates on its record, inside a stated tier ceiling
status: implemented
date: 2026-09-06
scope: stack
---

## Decision

Two mechanisms, one for policy and one for evidence:

- A **response tier** per condition or action class states the CEILING of
  what automation may do: verdict-only → investigate → propose → auto-apply
  with sampling. Today the probe's escalation-by-level is the only tier knob;
  this generalizes it into configuration an operator can read.
- Every propose-only automation (memory suggestions, silence proposals,
  auto-distill) accumulates a **visible record** — proposals, approvals,
  dismissals, later regrets — and graduation to auto-apply requires the
  record, not an argument. Auto-applied actions enter a sampling queue that a
  patrol reviews after the fact, and the review results feed the same record.

## Why

Anthropic's progression for new automation is shadow → earned trust →
risk-weighted sampling of automated approvals, with boundaries defined around
access and actions rather than around instructions. This family already holds
the propose-not-act half. What is missing is the ledger that would let a
propose-only path EARN auto-apply, and the after-the-fact audit an unattended
deployment needs in place of a human in the loop — nobody here answers in
real time, so sampling has to be asynchronous or it will not exist.

Crucially the record is built from labels this family already collects —
approve/dismiss presses, useful/useless rulings, the regret counter — and not
from agreement rates. The shadow-conclusion note stands: unlabelled agreement
measures the instrument. A graduation built on it would be 386 samples of a
model agreeing with itself, wearing a trust score.

## Consequences

- Per-automation counters surface first (most are queryable today); the
  sampling queue starts as a patrol brief, and becomes code only if the brief
  proves the shape.
- The tier config is itself a knob, so changing one is a measurement: replay
  the ledger or run the eval before flipping it, or this note repeats the
  mistake the rule-reuse note exists to prevent.
- Auto-apply for memory stays behind its red-team smoke regardless of how
  good the record looks. A track record does not answer the injection
  question — different axes, both required.
- Nothing here weakens a write gate. A gate moves only via tier config plus
  record, both of which are written down and diffable.

## Built (2026-09-06)

`hookprobe/automation.py`, and the shape held close to the note:

- The tier ladder `verdict_only < investigate < propose < auto_apply` is a
  declared ceiling per class (`HOOKPROBE_AUTOMATION_TIERS`), and the scattered
  auto-apply knobs now operate UNDER it — memory's `apply_safe` is gated by both
  its own switch and the ceiling, so an operator halts auto-apply in one config
  line without hunting the knob down. Defaults preserve today's behaviour
  exactly.
- The record (`automation-log.jsonl`) is wired at the two decision points the
  note named as labels the family already collects: memory accept/dismiss and
  remediation approve/reject. Nothing counts an agreement rate; every row is a
  human press or a sampling regret.
- `supports()` answers "would the record justify this ceiling", and `review()`
  the forward-looking "what has this class earned" — both advisory. NOTHING
  moves a tier on its own; the operator diffs the config. `GET /v1/automation`
  surfaces it; `POST /v1/automation/{class}/{id}/regret` is the one write, gated
  to the operator token so a run cannot label its own work.
- The sampling queue is a patrol brief (`examples/patrols/automation-sampling.md`)
  and not code, exactly as the Consequences section required — it becomes code
  only if the brief proves the shape.

What is deliberately still true to the note's constraints: memory auto-apply
stays behind its shape check regardless of record, the tier is a knob whose
change is a measurement, and no write gate was weakened — a gate moves only via
tier config plus record, both diffable.

DEFERRED: silence and distill are declared classes with default ceilings but no
record wire yet — their decision points are less clean (a silence proposal is
judged on the pipe side; a distill write has no human press). They surface in
the review with an empty record, which reads honestly as "nothing to graduate
on". Wire them when a clean label exists, not before — an empty record is the
correct state, and inventing a label to fill it would be the agreement-rate
mistake in a different coat.
