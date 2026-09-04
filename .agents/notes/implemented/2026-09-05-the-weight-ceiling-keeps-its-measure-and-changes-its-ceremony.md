---
title: The weight ceiling keeps its measure and changes its ceremony
status: implemented
date: 2026-09-05
scope: stack
---

## The question that was left

`scripts/assert_weight.py` raised hookrelay's ceiling three times on
2026-09-04 and said so in its own comment:

> THIS IS THE THIRD RAISE IN ONE DAY, and that is worth more than the number.
> The ceiling exists so the next 400 lines cost a conversation, and three
> conversations in a day is a formality rather than a conversation. Whoever
> touches this next should decide whether the measure should be per-module
> rather than per-service, and record that decision in .agents/notes rather
> than raising a fourth time in silence.

This is that decision, taken on the fourth raise.

## Decision

**The measure stays: raw source lines, per service.** What changes is what a
raise has to say.

A raise must now state the **split** the script already prints — how many of
the added lines are code — and answer the doctrine question ("is this a
property of a good pipe, or a judgment that belongs to a brain behind it?")
**for the code lines only**. A raise whose code count did not move is
bookkeeping and costs one line in the log. A raise carrying 250 new code lines
is the conversation the ceiling was built to force, and costs one.

## Why not per-module

Per-module ceilings would fire roughly eight times as often, each time for a
smaller change, and each firing would be a smaller conversation. That is more
formality, not less — the exact failure this note exists to answer.

The one thing per-module would catch that per-service cannot is a single file
growing into a god object. That is a complexity question, and the script's own
`WHAT THIS DOES NOT CATCH` section already says complexity is out of scope for
a line count:

> 200 lines of nested async retry logic and 200 lines of dataclass definitions
> are not the same 200 lines.

Adding per-module ceilings would be dressing a line count up as something it
says on its own face it is not.

## Why not code-only, which is where this nearly went

The measure looked wrong today, and for a real reason: the change that forced
this raise was four ledger-integrity fixes measuring **+36 source lines and −1
code line**. The service's code got smaller. Everything added was the record of
why. A ceiling that fires on that is firing on the wrong thing.

The file had already rejected the obvious fix, and the rejection holds:

> under a code-only count, deleting the docstring that records why the `http`
> stage must not fire during a dry run would BUY headroom. In this repository
> those docstrings are the decision records.

`wc -l` is also the whole of the verification story — a reader checks the claim
in one command, and a budget nobody can check by hand is a budget nobody
trusts. Both arguments survive contact with today's evidence. So the measure
that produced the false positive is still the right measure, and the answer is
to make the response proportionate rather than to change what is measured.

## Consequences

- A bookkeeping raise is now cheap ON PURPOSE, which is a loophole if the code
  split is not read. It is one number the script already prints; a reviewer who
  does not look at it has waved through the thing the ceiling exists to stop.
- Nothing enforces this. The rule is prose in a note and a comment in
  `CEILINGS`, exactly like the doctrine question it serves — a check that
  measured "is this argued well enough" would be worse than none.
- The ceiling still does not catch complexity, and per-module would not have
  either. A single file growing unreadable remains something a person has to
  notice.

## What this predicts

If the ceremony is right, the log in `CEILINGS` should start showing raises of
two visibly different kinds: short bookkeeping entries whose code count barely
moved, and long ones that argue a capability into the pipe. If a year of
entries are all short, the ceiling has stopped catching anything and the next
person should say so rather than raising a ninth time.

## Also on this raise

5100 → 5200, for the ledger-integrity fixes in
`hookrelay/hookrelay/store.py`: foreign keys enforced rather than declared, the
commit-and-announce pair made unreturnable-past, batched purge that gives the
write lock back, and `LIKE` escaping. Code: −1.
