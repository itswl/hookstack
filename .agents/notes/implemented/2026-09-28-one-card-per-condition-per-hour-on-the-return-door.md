---
title: One card per condition per hour on the return door — the fold, and the number it answers to
status: implemented
date: 2026-09-28
scope: hookrelay
supersedes: 2026-08-12-who-owns-noise-when-a-verdict-is-reused
---

## Decision

A `fold` pipeline stage: on a return door, a verdict the brain marked
`wake=yes` is delivered once per condition per window, and the same condition
judged again inside the window is skipped by name (`folded`), its trace naming
the card it folded into and how long after. The window is anchored on the card
that was delivered, not on the last repeat, so a condition firing every fifteen
minutes surfaces once an hour rather than never. A recovery is never folded.
`key` is `title` by default or a field the door extracts (the judge's `rule`).
The reference production config runs it after the wake filter with a one-hour
window. The digest at the hour's end — "N more, folded" — is the next step and
not this one.

This closes the question the 2026-08-12 note left open, with closure 2 of the
two it named: the pipe paces its return door. The objection recorded against
that closure — the brain's ledger no longer describes what was delivered — is
answered by the pipe's ledger describing exactly that, repeat by repeat.

## Why

Pilot zero ([[pilot-zero-read-back-and-the-order-of-the-next-ninety-days]]).
The judge's ledger from the retired production deployment, replayed on
2026-09-28:

| | |
| --- | --- |
| wake=yes cards, five weeks | 731 |
| from the loudest single rule | 65% |
| that rule's median gap between cards | 15 minutes |
| folded by one card per rule per 15 minutes | 33% |
| folded by one card per rule per hour | 54% |
| folded by one card per rule per four hours | 67% |
| distinct rules with any wake=yes card | 23 |

The 08-12 note's trigger was "repeated cards from one condition drawing a
complaint, or an operator muting a channel". The operator did not mute the
channel; they stopped reading it after two evenings, which is the same event
seen from the other side. The wake filter had already taken the `no` half
(272 cards a week); this takes the repeats of the `yes` half.

The doctrine question, argued for these lines only: the stage reads no
content. It reads that the same condition, by the door's own extracted key, had
a card delivered inside the window — a fact about the ledger, not about the
alert — and it stands only on a return door, where the brain has already
judged. On a front door it would be dedup by another name, and dedup's doctrine
says where that belongs. The pipe's ceiling was raised 5,700 to 5,800 for it, in
both homes, with this argument beside the number.

## Consequences

A deployment pinning the stage to a return door sees its interruptions fall by
whatever its own repeat share is; the ledger's `skip_code=folded` filter shows
exactly what did not go, each row pointing at the card that did. The judge's
board is untouched: every verdict is still judged and still counted there,
which is what keeps its noise accounting truthful. What a person no longer
sees is the restatement — the summary text of the repeat may differ (a count,
a percentage) and that difference is lost until the digest exists.

Not measured live: the alert shape runs nowhere today. The number above is a
replay, and the first week the shape runs again is the first real reading.

## Rejected

- **Closure 1, the brain returning a suppression signal.** The judge's reuse
  window is minutes; the loudest rule's cards were mostly re-judged and paid
  for, so a fold on `route: reuse` would have folded a fraction of this. And
  the brain saying "do not deliver" is the brain owning delivery, which the
  doctrine keeps in the pipe.
- **Widening the judge's reuse window instead.** Changes what the judge
  counts as one verdict, which changes its cost accounting to fix an attention
  problem.
- **A fold on the front door.** Dedup, already there, already ruled out in
  paired posture.
