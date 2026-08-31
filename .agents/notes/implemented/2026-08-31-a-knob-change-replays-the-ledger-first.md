---
title: A knob change replays the ledger before it ships
status: implemented
date: 2026-08-31
scope: hookjudge
---

## Decision

`scripts/replay_ledger.py`: replay the deployment's own retained traffic
through the judge as the ENVIRONMENT configures it, and diff importance and
wake against what production actually served. Latest AI-judged firing per rule
by default, loudest rules kept first under `--limit`; every first-draw mover
re-asked to a majority of `--votes`; the ledger is opened read-only at the
sqlite connection. Not a gate: a flip never exits red.

## Why

Two earlier notes already contained this tool without naming it. The
rule-reuse note ruled that turning that knob on "is a measurement, not a
config change" — but the only instrument offered was the golden set, which is
curated and cannot say what a change does to last month's real mix. The
shadow-conclusion note recorded that the offline form — mining the retained
ledgers — survives the shadow's retirement and needs no second live brain.
This is that offline form, built, and it carries the shadow's two findings
directly:

- 83%/77% measured self-agreement means a single-draw flip is likelier the
  coin than the config, so movers are re-voted and a majority that reverts is
  reported as the candidate disagreeing with itself, never as a difference.
- Unlabelled agreement cannot say who is RIGHT, so the tool refuses to be a
  gate. Recorded verdicts are the old configuration's own homework; the only
  rows read as labels are the ones a person ruled (`label_importance`,
  `mattered`), and those lead the report — a candidate that would drop a card
  a person said was worth it is the first line printed.

It is also the playbook's practice ("run evals whenever agent configuration
changes") pointed at the corpus every deployment owns instead of a benchmark
it does not.

## Consequences

- A replay costs a real bill: one draw per rule by default, `--votes` per
  mover. Any deployment that automates it should say so in its runbook.
- Only route='ai' firings are candidates. Rule-floor rows would measure
  keyword-vs-model (already known, disagree almost by construction);
  recoveries would re-litigate the firing they inherit from. Both refusals
  are tested.
- The recorded side is one production draw, noise included, and is never
  re-drawn. The diff answers "how would delivery have differed", not "which
  configuration is right" — the report footer says so, and anyone quoting a
  flip count as a quality score is misreading the instrument.
- Usage is documented in eval/README.md beside the golden-set instrument it
  complements, so the two are read as a pair: goldens gate, replays inform.
