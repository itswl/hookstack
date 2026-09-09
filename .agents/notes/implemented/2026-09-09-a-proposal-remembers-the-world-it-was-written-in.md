---
title: A proposal remembers the world it was written in, and refuses to run in a different one
status: implemented
date: 2026-09-09
scope: hookprobe
---

## Decision

Every remediation proposal now records a **cursor** — what was true about its
condition at the moment the steps were chosen — and the approval reads it again
before anything runs. Two fields:

* `recovered` — has the condition **ENDED**.
* `run_id` — has this investigation taken **another turn**.

If either has moved, the row goes to a new terminal status `superseded`, nothing
executes, and the refusal comes back into the chat as a report.

Checked in two places, deliberately:

1. `actions._open_proposals` does not **draw** an approve button whose cursor
   has already moved (and, while there, does not draw one past the 24h window
   either — an existing hole, since a follow-up turn re-delivers old proposals).
2. `remediation.approve` refuses the race the card cannot see, between the card
   being sent and somebody pressing it.

The refusal is **terminal**. There is no override, no second press, no force
flag.

## Why

The 24h `APPROVAL_WINDOW_SECONDS` was already here and its comment already made
the argument: *"a procedure is a set of commands chosen from evidence gathered
at one moment … approving it a week later runs a decision made about a system
that has since moved, and the operator pressing the button cannot see that from
the card."* But a clock is a proxy. It assumes the world moves at a rate.
`docs/containment.md` said so out loud in the row's own "does not stop" column:
*"An approval at 23 hours on evidence that went stale in one."*

The material to do better was already on disk and nobody was reading it.
`service.record_recovery` has annotated runs with `recovered_at` since the
recovery door existed; it starts no turn and costs nothing, which is exactly why
it needed its own cursor field — a recovery is invisible to every other signal
this service has.

**Why only two fields.** The thread's reply count and the re-fire count were both
considered, are both already recorded, and were both dropped: neither can move
without `run_id` moving too, because a reply, a re-fire and a follow-up press all
end up calling `continue_run`, which mints a new run id. A field that cannot fire
on its own is not a signal, it is a second copy of one. (`refires` is still read,
to say *why* the run id changed. That is explanation, not detection.)

**Why nothing reaches out to look.** The reviewed proposal asked for a live
"Probe（探活）" of the target's current state before a mutating call. That is
refused on this node's own terms: the production investigator holds **no
credentials** for the systems it writes procedures about, and a freshness check
that opened one would be a second, unaudited path to them — beside the allowlist,
beside the audit log, beside the click. Every field in the cursor arrives through
the pipe. The cost is written down rather than hidden: somebody fixing the target
by hand and saying nothing in chat is invisible to this.

**Why the refusal had to become a report.** This is the part a diff cannot
explain, and it nearly sank the feature. The chain is: bridge reads the press →
POSTs it to the pipe → the pipe **enqueues** a delivery and answers immediately →
the bridge repaints the card *"accepted and passed on"* and **strips the action
block**, because the token in those buttons is single-use. Only then does this
service see the press. So a refusal decided here reaches the operator through no
existing path at all: the card says it landed, the procedure never ran, and the
only trace is a log line. A gate whose refusal is invisible is worse than no
gate, because now the procedure did not run *and* nobody knows.

The budget breaker had the same problem and the same answer, which is the
precedent this follows: refuse, then say so as a report through the family loop,
in the processed-event dialect, into the same conversation. `superseded_report`
is shaped like `budget_report` for that reason.

**Why terminal, and why no override.** Because the button is already gone. "Refuse
once, let them press again" is a UI that does not exist on a card — its token was
spent on the press that got refused. What does exist, on the same card, is the
follow-up button: it re-investigates and proposes against the world as it is now,
which is the answer anyway. So the refusal says exactly that, and the module keeps
one rule instead of two — the same rule `stale()` already states.

Not recorded on the automation ledger, and that is a decision too: that ledger
counts what a **person** decided (approved / dismissed / regretted) and feeds the
graduation figures. A procedure the condition outran is not a human dismissal,
and counting it as one would quietly make the machine look more often overruled
than it was.

## Consequences

* A new terminal status, `superseded`. The UI renders statuses generically, so it
  needed no change; `work.py` treats it as neither open nor blocked, which is
  right — nobody can clear it and nobody should try.
* **A proposal stamped before this existed carries no cursor and is never judged
  to have moved.** Absent is not stale. An upgrade must not retire what is
  already waiting; the 24h window still bounds those.
* One extra `remediation.load` per click, to find the session before approving.
  A small JSON read on a human action.
* A superseded procedure produces a card. On a deployment with many proposals and
  a chatty recovery stream that is more chat traffic than before — the alternative
  was the silent refusal above, which is not a trade worth making.
* Twenty-second containment boundary. The "Approval window" row's *does not stop*
  column now points at it instead of describing an open gap.
* What is still not covered: two proposals from the same session, where approving
  the second after the first ran is unchanged behaviour. `_MAX_APPROVE = 3` bounds
  it and they are genuinely different procedures.
