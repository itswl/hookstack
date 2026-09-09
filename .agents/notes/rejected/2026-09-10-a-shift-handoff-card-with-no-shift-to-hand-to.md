---
title: A shift-handoff card — rejected while this deployment has no shift to hand to
status: rejected
date: 2026-09-10
scope: hookprobe
---

## Decision

Not building the automatic **shift-handoff card**: a projection posted into the
chat when somebody joins or acknowledges, summarising a long incident as *state
now / what I checked / what I ruled out / who approved what / suggested next
step*.

Rejected on the premise, not on the design. The operator was asked directly on
2026-09-10 whether this deployment has a rotation and answered: **no — it is
essentially one person watching.**

## Why

The idea is sound and the pain it names is real on a staffed rota: a new
responder facing a three-hour chat log has to reconstruct state by reading, and
that reading is pure friction. Larkin maintains a `previousSessions` chain for
exactly this.

But every part of it assumes somebody arrives. With one watcher there is nobody
to hand to, no acknowledge to hook, and no cognitive-overload moment to relieve
— the person reading the card is the person who was already there. What it would
add is another card in a chat that already receives a report per investigation,
a reply per follow-up, and now a notice per declined reply. **A card nobody needs
is not neutral; it is noise on the surface whose whole job is to be read.**

What already answers the underlying question here, for one person:

* the **work board** — `waiting_approval` and `needs_human` are literally "what
  should I look at first", and `abandoned` is deliberately kept out of that
  number so it stays useful in the morning;
* `/v1/work` groups every run of one `work_id` across nodes, with its artifacts,
  its re-fires and what it is still waiting on;
* the pipe's **`/timeline`**, which is the only place a whole chain is visible,
  because every handover goes through the pipe by construction.

The gap between those and a handoff card is a *narrative* — prose a person reads
instead of a board they scan. That is worth building for a reader who was not
there. It is not worth building for the reader who was.

Note that `hookprobe/handoff.py` already exists and is a different thing
entirely: node-to-node, an operator handing a finished plan to a work runner so
the copy-into-a-laptop-agent gesture keeps its audit. Nothing here changes it.

## Consequences

* **Reopen this when a second person starts watching**, and that is the whole
  condition — not "when the incidents get long", which they already are.
* If it is reopened, the trigger is the constraint worth deciding first: a group
  @-mention is reachable (the bridge already forwards those), a PagerDuty
  acknowledge is not reachable from here at all. The projection itself is a read
  over `/v1/work` plus the run texts, which is why the design half is cheap and
  the premise half is the whole decision.
* Recorded here rather than dropped, because the review that proposed it was
  right about the problem shape and a future reader should find the reason it
  did not ship, not a silence.
