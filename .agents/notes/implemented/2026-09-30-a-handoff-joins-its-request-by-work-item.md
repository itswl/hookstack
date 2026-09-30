---
title: A handoff joins its request by work item, and the work run says what it did
status: implemented
date: 2026-09-30
scope: stack
---

## Decision

- **The journey and the board gather by work item.** A hop that quoted nothing
  but carries a work id the pipe minted (`hr-<n>`) belongs to event n's chain.
  That is the plan-approved hop: the planner posts it after a person presses
  "hand off", with the plan's work item and no quote. The work run's report
  quotes the handoff, so it follows. `timeline.py` keys the chain that way and
  marks the hop `by_work`; `store.round_trip` walks it up and down.
- **A reply stays by quote.** `thread_context` calls `round_trip(by_work=False)`.
  A handoff starts a new conversation on the same work item.
- **The door does not quote.** `deploy/work.yaml`'s plan-approved door still sets
  no `correlation_id`. The reader-side rule covers old and new rows alike, and a
  console plan's work id is its session key, which must not become a quote.
- **The work run states what it did, as counts.** hookprobe's report carries
  `changed_files` (files in a diff that commits in the clone back; 0 when no
  commit backs it) and `blocked` (gaps it named). The work-notify door copies
  them with `probe_status`. The board and the morning card read the last work
  report after a handoff as:
  - carried out: files changed; ended well;
  - blocked: nothing changed and gaps named; waits on you;
  - failed: the run failed; red;
  - reported: neither count, as every report before this change.

## Why

The operator's two handoffs on 2026-09-30 each showed as two rows: the request,
stopped at "handed off" with "no fix was approved", and a "plan handed off"
row beside it. The pipe had the link all along. The handoff carried `hr-<n>`
as its work item, and nothing read it.

Gathering by work item everywhere would have changed where replies go. Under
the plan's card, the newest session in the chain becomes the work run's, and a
chat message would reach the one node holding a write credential without a
press. The test holds that.

"A report came back" is not "done". The morning handoff's run came back
`completed`, changed nothing, and named the permission it lacked. A board that
called it done would have hidden the one thing the operator had to act on.

## Consequences

- Reports written before this change read "work node reported" on the board.
  The drawer still shows their diff and gaps from the report text.
- A new kind of node that returns work under a pipe-minted work id joins that
  chain without configuration. That is the point: the pipe minted the id.
