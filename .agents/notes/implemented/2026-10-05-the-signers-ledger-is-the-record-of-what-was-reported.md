---
title: The signer's ledger is the record of what was reported
status: implemented
date: 2026-10-05
scope: stack
---

## Decision

The watch wrapper stops writing the node's `reported` cursor. The signer
records, for every signal it signs, the conversation and the cursor the scan
offered it at, and `scripts/assert_node_contract.py` reads that record
(`--signer-ledger`, which the timer passes from a read-only mount of the
signer's volume). Its first promise becomes "every conversation it reported
was signed by the signer"; the offered-only promise is unchanged and now reads
the round before alongside the current offer; "not further than it has been
read" is checked against the signer's recorded cursor. Without the flag the
checker reads a node's own books exactly as before, which is what the gate's
replay of the 2026-09-04 round still proves.

Alongside: the scanner's own notes (`scanner-notes`) are not a conversation
and the checker no longer expects anything to have offered them; the feed
statistics read delivered counts from the signer's ledger; `deploy/watch` is
linted by the gate.

## Why

The checker's own argument: a node that forgot to write its state is exactly
the node whose self-report cannot be trusted. It already read "what was
signalled" from the pipe's ledger for that reason; `reported` was the one
self-report left. And since the scanner took over the reading cursor, a missing
`reported` write no longer caused a duplicate send — it only caused the
checker to fire, as it did on 2026-09-30 18:20 on the operator's own manual
posts. A boundary that pages about bookkeeping it could do itself is noise.

Mounting the watcher's state into the signer so it could write `reported`
there was rejected: a second writer of one file, and the signer reaching into
the agent's volume for no gain. The signer already holds the fact; the fix was
to let the checker read it where it is.

## Consequences

- `work-data/watch-signer/signals.jsonl` is now an input to the contract
  check. The timer mounts it read-only; a missing file is "nothing signed since
  the snapshot", not an error.
- `WATCH_REPORTED_STATE` is gone from the compose. The node's state file keeps
  its old `reported` and `counts` keys, frozen; nothing writes them now.
- A signal posted to the pipe's watch door that did not pass through the
  signer — a laptop poster naming a `producer / conversation` origin — is now a
  broken promise. The laptop callers (`needs_you.sh`, `weekly_page.sh`) name no
  conversation, so they are not.
- Read back on the work machine the same day: the next tick's check runs with
  the ledger; the watcher's first signal after this records subject and cursor.
- Owed elsewhere: the isolated gateway mode has been read back only on this
  laptop's OrbStack engine. A Linux deployment's smoke should connect from a
  probe to `.1` of `probe_net`'s subnet and expect no answer at all; "refused"
  or a connection means the host is reachable and the mode is not in effect.
