---
title: The signer is the only promise left to check
status: implemented
date: 2026-10-06
scope: stack
---

## Decision

`scripts/assert_node_contract.py` retires, with its before/after snapshot of
the node's state, its three promises, its fixtures of the 2026-09-04 round and
the seven `CONTRACT_*` variables the timer carried for it. What replaces it is
one check, `scripts/assert_watch_signed.py`: every watch signal the pipe took
since the last tick is in the signer's ledger. The timer runs it once per tick
over the pipe's `/status` and the signer's `signals.jsonl`, keeps a stamp of
the last check, and posts a `low` signal from its own file when a conversation
was signalled that the signer never signed. The gate keeps an inverted replay
(the real September round against an empty signer ledger must fail).

## Why

The checker was written on 2026-09-04, when the node kept its own books and a
round had posted a signal and moved nothing. Two of its promises were about
those books. On 2026-10-05 the signer became the record of what was reported:
"the cursor moved" and "not further than read" then held by construction, and
the only promise with teeth left was "every signal came through the signer" —
which is a boundary check, not a bookkeeping one. A snapshot machine, a mount
of the node's volume into the timer and seven variables were being kept to
evaluate two tautologies. It had fired three times in a month, all three on
tooling changes and none on the agent.

This is the second of the four cuts the operator asked for; it is the one
that was made redundant the day before, so it went first among the code cuts.

## Consequences

- The timer's first tick after the deploy only sets the stamp: the pipe's
  ledger reaches back before the signer existed, and that history is not a
  finding. From the second tick on, a miss is a `low` card.
- `work-data/watch-timer-state/snapshot.json` and `.at` are orphaned on the
  work machine; nothing reads them. The node-state mount stays for the one-time
  seed of the scan's cursors.
- A laptop poster naming a `producer / conversation` origin would be reported
  as going around the signer, as before; the two laptop callers name none.
- `hookrelay/tests/test_watch_signed.py` holds the cases; the old test file and
  the September fixtures are gone, except the one ledger the inverted replay
  still uses.
