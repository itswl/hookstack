---
title: The cursor moves when the round is known to have landed
status: implemented
date: 2026-10-19
scope: stack
---

## Decision

The watch scanner (`deploy/watch/watch_scan.py`) no longer commits a round's
cursors when it hands the digest over. A round that fires stages them under
`staged` in `scan.json`, and Jira runs against a staged copy of its state. The
next tick settles the previous round first, before the window check:

- the timer writes what the pipe answered (`round.json`: posted, event id,
  outcome, fired-at) after each POST (`patrol-timer.sh`);
- the scanner reads the node's run record for `probe:watch-due:<event id>`
  under the timer's read-only `/node-state/results` mount;
- `completed` commits the staged cursors and the Jira copy; `failed`, a POST
  that failed, an answer other than `routed`, a record that never appears
  within `WATCH_RUN_TIMEOUT` put the cursors back and the next scan re-offers
  from them with a line saying so; a run still going holds the tick;
- after two replays and an hour since the first failure, only the first lost
  round's content is skipped (its cursors are committed) and the skipped span
  is named in the digest until a round lands. Jira is never skipped.

`feeds` in `scan.json` therefore always means "committed"; `offered` and
`previous` keep their meaning for the signer and the poster.

## Why

The cursor was the agent's promise written before the agent ran. A model error,
a dead gateway, a refused delivery or a timer restart between scan and POST all
lost that round's messages with no trace — the next tick scanned from the new
cursor as if the round had been judged. A second deployment running a copy of
this scanner measured it on 2026-10-12: two rounds lost to one configuration
fault, and the fix there (stage, then reconcile against the job's run record)
is the shape taken here with the pieces this family has — the pipe's event id
and the node's run record, both already on the timer's mounts.

Holding the tick while a run is in flight is the price of not offering the same
messages twice; the node's runs finish in minutes and `WATCH_RUN_TIMEOUT`
bounds the wait. Giving up after two replays and an hour is what keeps a poison
round from stalling the feed forever, while three failures in fifteen minutes
still read as an outage to retry.

## Consequences

- `scan.json` gains `staged`, `replay` and `skipped`; `watch_feed_stats.py`
  and the signer read `offered`/`previous` and are unaffected.
- A deployment without `WATCH_ROUND_FILE` or without the node's results mount
  settles every fired round as lost next tick and replays it: loud, and the
  compose names both.
- A storm-suppressed or unrouted round is replayed rather than counted as
  judged, which is the correct reading of "the pipe did not run it".
