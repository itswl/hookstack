---
title: One alert, one page — and the pipe still keeps only its own books
status: implemented
date: 2026-09-28
scope: stack
---

## Decision

The pipe's board gets a **journey** tab: type any handle an alert left behind
and read everything the ledger holds about it, top to bottom — received,
judged, delivered (with what each card asked), pressed, reported, and what the
condition did afterwards. `GET /trace/{ref}` does the resolving: an event id,
the `hr-<id>` the pipe stamps on egress, a session key or work id a return door
extracted into a field, or the platform id of a card the pipe sent. The answer
gained `recoveries` and `refires` — later events of the same source and title
within a day. A chain in the timeline and an event in the list both carry a
`journey` button, and `#journey=<handle>` is a link somebody can send.

Two things were fixed on the way because the page made them visible:

- The investigator now keeps the pipe's `X-Hook-Correlation-Id` apart from the
  work id (`meta.correlation_id`) and echoes it on its report, and a press
  resolves by it. Before, every button cut from a REPORT card carried the
  report's own event id and correlation, which the investigator had never seen:
  a follow-up pressed on an investigation card answered "no run", and a
  `useful`/`useless` forwarded to the judge named a correlation no judgement
  carried. Only `approve`, which resolves by proposal id, worked from that card.
- The ledger's copy of a delivered card no longer holds the live action token.
  `/trace` served `sent_body` under a read guard that is open until a token is
  configured; anyone who could reach the board could press "approve" on
  somebody else's behalf for a day. Tokens are redacted by shape, the label
  beside them stays.

The pipe's ceiling rises 5,800 → 5,900 (+96 source, +58 code; the argument is
in `scripts/assert_weight.py`).

## Why

The operator's words: 查全流程现在太分散了. On the running work deployment
"what happened to this" meant the pipe's board on one port, three
investigators' consoles on three more, and the chat thread — and the pipe's
own trace view was built to COMPARE two brains' answers to one input, not to
read one alert's story in order, and could only be asked with an event id
nobody outside the pipe holds.

Two notes argued against a cross-node page and both still stand:
[the-board-is-the-overview](../proposed/2026-09-08-the-board-is-the-overview.md) and
[the-overview-is-a-signal](../proposed/2026-09-09-the-overview-is-a-signal-and-it-fans-out.md).
What they refused was an AGGREGATOR: the pipe calling out to probes and
interpreting their boards (a fifth job beside receive, route, deliver,
account), CORS on consoles that hold bearer tokens, and nodes holding each
other's credentials. This page does none of that. The pipe stitches rows it
already holds, by identifiers it minted or copied — the same ones the timeline
and `thread_context` already read — and where a node's own account begins
(the run, its proposals, whether the fix held) the page links to that node's
console and stops. The first note named its own expiry: "if the honest reply
needs more than one board, the board has stopped being the front page". The
operator said so; this is the reply that fits inside the doctrine.

"Afterwards" belongs in the pipe because it is the pipe's fact: a recovery the
source stated and a re-fire are events, keyed the way the fold stage keys a
repeat, and the pipe reads no content to say "it fired again". What the
investigator concluded FROM them (`held`, `did_not_hold`) stays the
investigator's — see the second step this note leaves open.

## Consequences

- One address answers "what happened to this": `/#journey=<handle>` on the
  pipe, for a session key from a console, a card id from a chat, a work id
  from a handoff, or the id on a card's footer. Handles are matched, never
  read; an unknown one is a 404, never the newest event.
- The page is the pipe's account and says so on its last line. Whether an
  approved fix held is one link away, on the node that ran it. The step that
  closes that gap — the investigator posting the OUTCOME through its return
  door, so it becomes a card in the thread and a row the journey shows — is
  agreed and follows in its own change.
- The stale line "hookprobe never sees the pipe's correlation id" was in three
  configs and one docstring; all corrected. A door written for an older
  investigator keeps working: `meta.event_id` is still echoed and the pipe
  resolves the bare id and the `hr-` form alike.
- `refires` on a source whose title repeats by design (a timer's tick) lists
  the next ticks as re-fires. Bounded to a day and a dozen, labelled as what
  they are; the page reads it as "fired again", which for a tick is true.
- The redaction is by token shape (`<base64url>.<64 hex>`), so a future token
  format that changes shape reopens the leak. The test that pins it mints a
  real token; change one, change both.
