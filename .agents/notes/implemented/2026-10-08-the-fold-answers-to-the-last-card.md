---
title: The fold answers to the last card — what a person was told decides what folds
status: implemented
date: 2026-10-08
scope: hookrelay
---

## Decision

The fold stage of [[2026-09-28-one-card-per-condition-per-hour-on-the-return-door]]
now anchors on what the person was last told, not on whether an event is a
recovery.

- **A recovery the person is waiting for goes at once.** If the condition's
  last card said firing, its recovery is never folded, so an ordinary firing
  and its end are two cards as before.
- **Anything else inside the window folds**: a repeat, a recovery after a
  recovery card, and a firing after a recovery card.
- **A firing held after a recovery card is settled.** Once a minute the worker
  loop looks for a held firing whose condition's last card is a recovery and
  that has no recovery after it a base window later. The first such firing
  walks the stages after the fold, the walk an arriving event takes, and goes.
  A silence in force by then holds it under the silence's own code instead.
  The decision is rewritten only while it is still held, in one transaction
  with its deliveries, so two sweeps cannot both send it.
- **The window widens** with `max_window_seconds`. It doubles with each card
  in the condition's current run, from the base up to the ceiling, and cards
  more than twice the ceiling apart start a new run at the base.
- **The card that goes counts what it stands for.** `fields.folded` reads
  like "3 more since the last card, over 46 min", and a bridge card shows it
  under the brain's details.

The reference alert config runs it at one hour widening to four.

## Why

Building the digest meant replaying the retired production deployment's judge
ledger again, and the replay disagreed with the 09-28 note. Its 54% had been
computed without the recovery exemption the stage shipped with. The loudest
rule alternated firing and resolved at a fifteen-minute median gap, so every
recovery went, re-anchored the hour and broke any run, and the stage as
shipped folds 24% at any window. The same replay showed the shipped rule could
hide the one event a person must not miss: a firing inside the hour after a
recovery card folded into that card, so the last card said "ended" about a
condition that had come back.

Replayed on 731 wake=yes cards over five weeks:

| rule | fewer cards | settled late |
| --- | --- | --- |
| as shipped 2026-09-28, any window | 24% | none, and some re-fires never told |
| this, fixed one hour | 36% | 4 |
| this, one hour widening to four | 55% | 5 |
| this, one hour widening to eight | 67% | 5 |

The settle test is "no recovery after it", not "still the condition's latest
event". Both give the table above on this ledger. The second lets a condition
that comes back and keeps re-firing restart the clock with every firing, so
the person hears only when the widened window runs out, up to the ceiling
later.

The doctrine question: everything added reads the pipe's own books for the
condition key the stage already used — which card went, whether it was a
recovery, what was held and when. Nothing reads what an alert says. It is
pacing, deliver and account, and it stands only on a return door where the
brain has already judged. The pipe's ceiling went from 6,050 to 6,300 lines
for it, with the argument beside the number.

## Consequences

- A settled card arrives a base window after the firing it carries, plus up to
  a minute. That delay is the price of not sending the flapping half again.
- The digest counts; it does not restate. A repeat's own summary text is still
  lost, and the judge's board still has every verdict.
- Any later recovery of the condition blocks a settle, whatever happened to
  that recovery downstream, because the condition ended.
- The stage reads back `(doublings + 1) × 2 × ceiling` of the condition's
  ledger: a day at one hour widening to four.
- The sweep runs in every relay and does nothing without a fold stage. The
  work stack has none.
- Not measured live. The alert shape runs nowhere today, so this is a replay,
  and the first week the shape runs again is the first real reading.

## Rejected

- **Folding a flapping condition's recoveries too.** Fewer cards, 58% at one
  hour to four, but 41 settles instead of 5. Each settle is a card an hour
  late, and the person reads "ended" for an hour about a condition that
  never stopped.
- **A thirty-minute base widening to four hours.** 51% with 23 settles.
- **Settling a held recovery.** One after a recovery card says nothing the
  person has not read.
