---
title: A wrong number costs whatever acts on it, not however wrong it is
status: proposed
date: 2026-09-10
scope: stack
---

## Decision

Before changing, trusting or quoting any figure this stack produces, name its
readers and say what each one DOES with it. The answer sorts into three, and the
severity of being wrong follows the sort rather than the size of the error:

* **A display.** A person reads it and decides nothing automatically. Being
  wrong costs a misled reader, and the fix can be a label.
* **A control.** Something acts on it without asking. Being wrong costs whatever
  that thing does — and a label fixes nothing, because the label is not what it
  reads.
* **A ledger a control later reads.** Writing to it is acting on the control,
  one hop delayed. This is the one that gets missed, because writing looks like
  recording.

The test is not "is this number wrong". It is "what happens next because of it".

## Why

Five cases in two days, and the pattern only became visible when they were put
beside each other. Two of them I got right for the wrong reason.

**The budget ceiling that could not bind (`7b6b060`).** After three turns and
93,388 tokens, `/v1/budget` read `spent_usd 0.0 · remaining_usd 1.0 ·
exhausted false` beside `unpriced_turns 3`. Every figure honest alone. The first
fix was to stop printing headroom nobody measured — a display fix, and correct
as far as it went. But `window_spend()` is also read by
`service.budget_state()`, which the event door compares to the ceiling before
spending money. So the wrongness was not that an operator was misled; it was
that a **control had no input** and could never trip. That is why the second fix
had to be pricing from tokens (`dbf0c51`) and not better wording.

**The CLI's cost estimate the breaker was reading.** Same control, different
error: not absent but from the wrong table — the agent CLI prices Claude models
and this deployment is not billing one. A display would have survived the error;
a breaker acting on it will not.

**`reject()` on an expired proposal.** Rejecting five stale remediation
proposals to make the approvals count read correctly looked like tidying a
display. `remediation.reject` calls
`automation.record(workdir, "remediation", proposal_id, "dismissed")`, and that
ledger is what graduation reads to decide whether a procedure may run
unattended. Five fabricated dismissals would have moved the input to a control
that decides what runs without a person. I argued against it from how the board
READS — true, and weaker; hookstack-b5 supplied the reason that decided it.

**The judge's price constants, as the contrast that proves the rule.**
`hookjudge-b` priced 31 identical calls 15% away from the live judge, on
DeepSeek's rate card. Equally wrong as a number — and it cost nothing, because
nothing acted on it: the arm had `HOOKJUDGE_RETURN_URL=none` and its ledger read
`returns: {skipped: 53}`. The same error on the live judge would have reached
the weekly page and the cost argument. Wrongness did not set the cost; the
reader did.

**And the inverse, which is mine (`1ca70fd`).** `guard_trips` and
`output_secret` were both correct and both had no reader at all — a turn record
and a JSONL nobody opens. A number nothing acts on is not safe, it is inert: it
buys nothing until something reads it, and recording it can feel like finishing
the work. I added both the same afternoon I told the operator this project's
deepest problem is generating judgements nobody reads.

## Consequences

**Three questions before touching a figure**, in this order: who reads it, what
do they do without asking, and does anything WRITE to it that a control reads
later. The third is the one that hides — `reject()` reads as a display action
and is a ledger write.

**A label is a fix only for the first kind.** Yesterday's "priced, not billed"
work was right for the weekly page and insufficient for the breaker, and both
were true at the same time about the same number. Naming which reader a fix
serves stops that being confusing.

**Recording a number is not finishing it.** Every measurement added here should
name its reader in the same change, or say plainly that it has none yet and
why — see `1ca70fd` for what the alternative looks like from the outside.

**This note is a formulation, not a mechanism, and nothing enforces it.** It is
written down because four of the five cases above were found by a person
noticing, and the fifth by a peer checking a claim rather than taking it. If it
turns out to need a check, the shape would be per-figure: every number on a
public surface names its readers where it is computed.
