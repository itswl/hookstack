---
title: The verdict on a fix is told where the report went
status: implemented
date: 2026-09-28
scope: hookprobe
---

## Decision

When an executed procedure's verdict is in — `held` by a recovery, `did not
hold` by a re-fire inside the window, or `held` weakly by the window closing
with nothing said — the investigator posts ONE notice through its return door,
the same door the report took: session `probe:outcome:<proposal id>`, titled
`<alert> · fix held` or `<alert> · fix did not hold`, `event_type:
remediation-outcome`, into the same thread (`thread_root`), under the same work
(`work_id`) and against the same chain (`correlation_id`). Held by a recovery
is also stated as a recovery (`is_recovery`), so the card takes the recovery's
colour. A verdict card offers no buttons.

The recovery and re-fire doors tell theirs the moment they stamp the row. The
quiet third verdict is found by `sweep_outcomes`, run at boot and once a
minute; the same sweep re-tells a verdict whose notice never got out. Told
once per procedure: the notice is idempotent on its key.

`_notice` grew a `status`: every notice before this one was a refusal and
failure-shaped; a fix that held is the one piece of good news this node can
bring and travels `completed` with no error.

## Why

The 08-31 note built the contract — exit 0 is not recovery; a procedure waits
for the condition to answer — and the answer landed on the row and the work
board. Nobody reads either from a phone. The card in the chat said "approved
and passed on" and stopped; the pipe's journey of the alert, built today, ended
at the press with a line saying "whether the fix held is that node's own
account". The person who approved a command at 3am found out whether it worked
by opening a console, or never.

Every piece was already there: `_notice` delivers report-shaped messages where
the work lives (cooling, superseded, unanswered), `evidence()` stamps the row
once, `outcome()` says the verdict in words. What was missing was the call —
and a clock for the verdict that arrives as nothing.

## Consequences

- A thread now ends: investigation → approve → **fix held**. The pipe's
  journey ends the same way, one line after the recovery, and the smoke asserts
  the order (the verdict follows the recovery it rests on) and the count (two
  cards, one per dialect, for the one procedure the rehearsal approves).
- The weak form is told as weak. "No re-fire within the window" is what is
  known, and the card says thin evidence, not success. A deployment that wants
  the strong form still needs a target re-read per runbook, which the 08-31
  note named as the thing that would change the answer.
- A verdict card carries no buttons, as no notice does: `actions.declare`
  already returns nothing for a run whose meta says `notice`, and the outcome
  notice inherits that. The ruling belongs to the investigation, one card up.
- The sweep reads up to two hundred rows a minute from disk. On a node with
  more executed procedures than that in its retention window the oldest go
  untold; nothing on any deployment has come within a hundred of it.
- Console-born runs get no verdict card, like every other notice: the console
  shows the row.
