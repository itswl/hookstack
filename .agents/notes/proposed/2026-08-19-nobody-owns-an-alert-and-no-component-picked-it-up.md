---
title: Nobody owns an alert — the pipe disclaimed it and no component picked it up
status: proposed
date: 2026-08-19
scope: stack
---

## Decision

Not built, and not to be built by accident. There is no on-call rotation, no
assignee, no acknowledgement deadline and no escalation in this family. An alert
is judged, possibly investigated, delivered to a channel, and then the family is
done with it — whether or not a human was awake.

If ownership is ever added it needs a component and a decision record, because
it changes what this family claims to be. This note is the record that the gap
is known and deliberate, so the next person does not bolt half of it onto the
pipe.

## Why

`hookrelay/README.md` states the pipe's position plainly: "no SLA, no on-call."
That is a correct scoping decision for a content-blind pipe. What never happened
is any other component picking the responsibility up, so the honest description
of the family today is: it makes an interruption *good*, and then hopes somebody
is holding the phone.

For a small team with one shared channel that is genuinely fine, and pretending
otherwise would add a rotation nobody asked for. The reason to write it down is
that it is the ceiling on who can adopt this. The moment two teams share a
deployment, "who is this for" is the first question, and the answer is currently
"whoever reads the channel".

The card actions shipped on 2026-08-19 make the gap sharper rather than smaller.
A press is now recorded with an opaque `actor` — so the ledger knows that
*somebody* acted, and still has no opinion about whether it was the right
somebody, or what should happen if nobody does.

## Consequences

- The cheapest honest step is not a rotation: it is an **unacknowledged
  escalation** — if no card action arrives for an alert of a given importance
  within N minutes, deliver it somewhere else. That needs no identity model at
  all, only the `card_actions` ledger that now exists and one timer, and it
  answers the actual failure ("nobody was awake") without claiming to know who
  is on call.
- A real rotation means a person model, a schedule, timezones and overrides. That
  is a product in itself and would dwarf every component here. If it is wanted,
  integrate with something that already does it rather than growing one.
- Whatever is chosen must not land in the pipe. Ownership is judgement about who
  should care, and the pipe is the component that deliberately understands
  nothing.

## Rejected

- **A rotation in hookrelay.** It is content-blind and says so; this would be
  the largest exception ever carved into that.
- **Treating a card press as an acknowledgement.** Tempting, since the data is
  now there, but pressing "not worth it" is not the same as taking ownership,
  and conflating them would make the attention numbers mean two things.
