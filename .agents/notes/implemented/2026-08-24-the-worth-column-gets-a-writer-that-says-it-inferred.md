---
title: A patrol may fill the worth column, and the column says the verdict was inferred
status: implemented
date: 2026-08-24
scope: hookprobe
---

## Decision

A patrol reads unruled runs and PROPOSES `RUN-RULING:` lines; the service lifts
them out of the report and files them with `actor="patrol:<session>"`, which
`runs.INFERRED_BY_PREFIX` recognises. `rulings_since` returns a fourth number —
how many of the ruled ones were inferred — and `/v1/budget` prints it, so the
worth sentence reads "12 of 19 ruled found the cause (7 inferred, not a
person's)". An inferred verdict carries `ruled_why`, persisted, because a verdict
nobody can audit is worth less than no verdict.

`record_ruling` is now the only writer. The bulk door added a second one
(`rule()`) that skipped `_board_changed()`, so a person clearing a backlog
through it left the board stale until the next poll.

## Why

The cost of an investigation has been measured to the cent since the ledger
existed; the worth side read `ruled_useful: 0` on a deployment holding 144
finished runs. The bulk door made that column reachable, and reachable is not the
same as filled — this service's own notes already record what happened to the
per-item path: "nobody presses the buttons on the cards". Building a door and
waiting is how the field got its first 0.

So the patrol infers. The danger is obvious and is the whole reason for the
fourth number: a worth figure is quoted at somebody deciding whether to pay, and
one that merges a model's opinion of itself with a person's judgement is worse
than the `0` it replaced, because `0` at least read as an absence. The judge side
settled the identical question the same way — infer, and say it is an inference.

The posture matches every other marker here: the patrol holds no token and does
not write. It reads run reports, which are downstream of attacker-influenced
alert payloads, so it proposes and the service signs. A run ruling gates nothing
and spends nothing, so there is no far end and no credential — which is exactly
why it must not be confused with `hookprobe.rulings`, the condition ruling that
DOES have teeth. Two kinds of ruling, opposite directions, one service: the
human-facing door is `POST /v1/runs/rulings`, nested under the resource it
annotates so the bare word is never the address.

## Consequences

The bar in the patrol is deliberately narrow, and most of it is refusals: no
ruling on an errored or stopped run (that blames the investigator for an
outage), none on a report not read in full, and never a ruling based on cost —
an expensive run that found the cause is useful and a cheap one that found
nothing is useless, which is the entire reason both numbers exist.

Scheduled Wednesdays, one day BEFORE the Thursday condition patrol: a cluster of
`useless` runs on one condition is evidence that patrol can then read on its own.

Found while wiring this: `rulings.extract` promised in a comment that a malformed
marker is "stripped either way", and it was not. The nothing-filed path returned
early with the text untouched, so a report whose EVERY marker was malformed kept
them and rode them out to a chat card — the exact failure the marker-not-JSON
pattern had been written to prevent. Fixed in both modules, with the note now
naming the failure rather than going quiet, since silence reads like a model that
ignored the instruction. No existing test covered it.

## What would change the answer

- Inferred verdicts diverging from the operator's own reading. The reason is
  stored on every one, so the check is possible; if it happens, the patrol's bar
  tightens or the inference stops being counted as `ruled` at all.
- A person actually ruling at volume. Then the inference is redundant and should
  be switched off rather than left to pad the number.
- Anything starting to GATE on a run ruling. It gates nothing today, and that is
  what makes an inferred verdict safe. The moment it does, this decision has to
  be re-argued from the beginning.
