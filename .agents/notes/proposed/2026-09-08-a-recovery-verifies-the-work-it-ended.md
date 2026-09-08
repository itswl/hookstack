---
title: A recovery verifies the work it ended — and stops buying a turn to say so
status: proposed
date: 2026-09-08
scope: hookprobe
---

## Decision

The event door now recognises a **stated** recovery and records it on the
investigation of the same condition, instead of starting or continuing one. It
is the third thing that can verify a piece of work (`work.py`), the weakest of
the three, and the only one that needs nobody.

## Why

**The number on the board was true and nearly always zero.** `verified` counted
a human ruling or a procedure that ran clean. This deployment is unattended by
design, nobody rules, and remediation is rare and gated — so the north star,
"finished, verified, and it never had to stop and ask", had almost nothing it
could count. Adding a third source is not softening the definition; it is
answering the question with a signal that already existed and was being thrown
away.

**The signal was already arriving, and being misread.** The pipe sets a
top-level `is_recovery` when a source template states it, and `judge-notify`
does. Nothing in hookprobe read it. A recovery therefore took the ordinary path:
matched the firing by (source, title) inside the coalescing window and bought a
**re-fire turn** — a paid message telling the model the condition had fired
again when it had in fact cleared — or, outside that window, funded a whole new
investigation of something already over. Both are wrong in the same direction.

**Identity was already solved, by the judge.** `condition_title()` strips the
"it ended" decoration before sending, precisely so a card does not read
"✅ Resolved · [RESOLVED] …". The side effect is that a firing and its recovery
reach this door under one condition name, which is why (source, title) matching
is exact here rather than a heuristic.

**Checked before the level gate.** A recovery inherits its firing's importance.
On production all eighteen in the window arrived below the escalation bar, so a
check placed after the gate would have recorded none of them. A condition ending
is worth recording whatever the number says; it is not a request to spend.

**Never inferred.** Only a stated flag counts — top level, or `fields` for a
door configured the other way. Guessing "resolved" from a title is a judgement
about content, which is the one thing this service leaves to the brain that owns
it, and that brain has already made it.

## Consequences

**`verified_by` now has three values and they are not equal.** `ruling` (a
person agreed) outranks `remediation` (its own procedure ran clean) outranks
`recovery` (the episode ended). The board prints which, and the operating guide
says plainly that a recovery is not proof the investigation was right or that
the agent caused the ending — a flapping alert clears on its own, and the
judge's self-heal figures already say how often that happens here.

**Recoveries stop costing money.** Small on this deployment, because its
recoveries have been low and medium, but the path that would have spent was
there for the first high-severity condition to end.

**A day is the reach, as a constant.** A condition that ends within a day of
being investigated is plainly the same episode; one that ends three days later
is verifying an investigation nobody is still reading. Not a knob until someone
has a reason worth writing down.

**The north star can now move on an unattended deployment.** Whether it does is
the measurement worth watching, and if recoveries turn out to verify nearly
everything, that says the alerts being investigated are mostly self-clearing —
which is a finding about the alert set, not a reason to distrust the number.
