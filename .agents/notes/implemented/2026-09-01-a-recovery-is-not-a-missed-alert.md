---
title: The deploy gate stops counting recovery payloads as missed alerts
status: implemented
date: 2026-09-01
scope: hookjudge
---

## Decision

`scripts/eval.py` reads `Incoming.is_recovery` for every row and keeps recovery
payloads out of `missed` — the number `--gate` fails on. They are still scored
and still reported, as `recovery_cases` and `recovery_under_called`, so nothing
is hidden; they just no longer stop a deploy.

The gate line now names `firing_cases` rather than `cases`, and prints a
warning below `MIN_GATE_FIRING_CASES = 15`, because the difference between the
two numbers is exactly how much of the dataset the gate ignores.

## Why

A label states what the CONDITION is worth. A recovery says that condition
ended, and production never asks the judge to rate one: the recovery route
reuses the firing's verdict. But the eval harness calls `rule_verdict` /
`ai_verdict` directly, so the recovery route never runs, and a recovery row was
being scored on a code path the product does not have.

That was not a rounding error. The dataset is 23 recovery rows out of 32, and
every one of the rule route's five "misses" was a recovery — the gate was red
on correct behaviour, and had been since the set grew. A gate that cannot go
green is one people learn to pass with SKIP_EVAL=1, which is documented as the
emergency hatch and would have become the routine.

Two firing rows were genuinely under-called, before and after the importance
rubric was rewritten — the rewrite did not move this number. Reviewing them
found the labels wrong rather than the judge: a certificate rule whose name
says "Expired" carries days-REMAINING as its value, and at 12 days with a daily
repeat it is not an act-now alert. Both were relabelled to medium with the
reasoning in their note.

The rejected alternative was to lower the five disputed recovery labels. It
would have turned the gate green on the same day while leaving eighteen other
recovery rows labelled the same way, and it would have encoded "a recovery is
worth less than its condition" into labels that exist to say what the condition
is worth.

## Consequences

- The gate reasons over 9 rows. It reports that on every green run until the
  dataset grows; the honest reading is that it is thin, not that it is passing.
- Adding firing payloads is now the highest-value work on this dataset. A
  recovery row costs a model call and gates nothing.
- The deploy host's copy of the dataset had drifted to an older 8-row version
  while the 32-row set lived only on a laptop, so the gate was running on a
  quarter of the set. Both are now the same file; a shared home for it is still
  missing, and it cannot be the repository — it holds real alert text.
