---
title: A patrol's ruling does not verify the work — the north star was grading itself
status: implemented
date: 2026-09-28
scope: hookprobe
---

## Decision

`work.py` counts a run as verified by a ruling only when the ruling is a
person's. A `ruled_by` carrying the `patrol:` prefix (`runs.INFERRED_BY_PREFIX`)
settles the ruling as owed — the patrol answered, and asking a person who never
answers is noise — but sets neither `verified` nor `verified_by`. The board's
`verified` count, the weekly page's "Verified n% of completed work" and the
north star `closed_unattended` therefore count only three witnesses: a person's
ruling, a procedure whose every step exited 0, or the condition ending.

The patrol keeps ruling. Its verdicts still withdraw a runbook every case of
which was ruled useless, still feed the "worth" line on the weekly page, and are
still labelled there as inferred. What changes is that they no longer count as
anybody saying the work was any good.

## Why

Read back from the production archive on 2026-09-28, the deployment having run
from 2026-08-03 to its retirement on 09-23:

| what the archive holds | number |
| --- | --- |
| investigation runs | 339 |
| runs with a ruling | 98 (55 useful, 43 useless) |
| of those, `ruled_by` a patrol | 98 |
| of those, `ruled_by` a person | 0 |
| report cards with the ruling pair delivered to the chat (last 2.5 weeks retained) | 86 |
| presses on that pair | 0 |

The board read `run.ruling == "useful"` and wrote `verified_by = "ruling"`
without looking at `ruled_by`. So the weekly page of 2026-09-21 said
**Verified 75.0% of completed work** and **Closed with nobody stepping in: 12
(70.6%)**, with the words "a person's ruling" beside the first, on a week in
which no person had ruled anything. The rulings line further down the same page
had distinguished the two since 2026-09-14 ("inferred, not a person's" —
[[the-worth-column-gets-a-writer-that-says-it-inferred]]); the two boards
disagreed about the same runs for two weeks and nothing said so.

`runs.py` has carried the reason for the prefix since the patrol was written: "a
column that mixed a model's inference with a person's judgement would answer the
adoption question with the model's own opinion of itself". The board was that
column.

## Consequences

The north star drops, and that is the true reading: on the retired deployment it
would have counted only the recovery-verified share, because a person never ruled
and the remediation door never opened
([[pilot-zero-read-back-and-the-order-of-the-next-ninety-days]]). Zero remains a
real answer here — the module says so already: "it says the verification loop is
not closed".

`unruled` on the board now describes fewer runs than a person could still rule,
because a patrol-ruled run is not offered again. That is deliberate today: the
one deployment that exists has nobody answering, and a debt nobody pays is
noise. If a person starts pressing, revisit whether an inferred ruling should
leave the debt open rather than settle it.

The 09-21 weekly page in the archive is the regression fixture: replaying its
work items through this code must not produce a `verified_by = "ruling"` row.
