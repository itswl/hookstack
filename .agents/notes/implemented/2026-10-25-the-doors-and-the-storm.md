---
title: The doors and the storm — an action secret, a bounded judge, and a patch name that cannot leave its directory
status: implemented
date: 2026-10-25
scope: stack
---

## Decision

The second batch from the same review, all at the point the promise is consumed.

1. **The investigator's card door has its own credential, and `approve` fails
   closed without one.** `HOOKPROBE_ACTION_SECRET` signs `/hooks/action` and
   falls back to `HOOKPROBE_EVENT_SECRET` so an existing deployment keeps one
   secret. With both empty the door is still unsigned for followups and rulings
   (the boot warning says so), but `approve` answers `503`: a run inside the
   container can read a proposal file and its hash, and an unsigned press would
   be indistinguishable from a person's.
2. **The judge bounds how many model calls are in flight.** `HOOKJUDGE_MAX_INFLIGHT`
   (default 8) is a semaphore around the judgement itself. The per-identity lock
   already serializes restatements of one condition; it said nothing about a
   storm of a thousand different conditions, each entitled to a paid call. The
   excess queues, and every verdict is still recorded.
3. **Every judge writer takes the write lock, and the return worker looks at the
   oldest attempt first.** `set_label`, `record_mattered`, `mark_return`,
   `record_ai_ruling` and `purge_older_than` committed without it, so another
   task's commit could publish `record()`'s burst UPDATE before its INSERT.
   `pending_returns` ordered by id, so fifty rows inside their backoff could
   hide a newer row that was due; it now orders by the attempt clock.
4. **A patch artifact's name cannot leave `patches/`.** The result file was
   sanitized; the patch was written from the raw session key, so `../` in a key
   wrote outside the directory. `_patch_name` replaces unsafe characters and
   adds a digest when the key had to change, so two rewritten keys cannot
   overwrite each other.

## Why

Each is a review finding verified against the source. The action door was the
one path where the absence of a secret still let the component that reads
attacker-influenced text execute an allowlisted procedure; the judge's cost
story had no concurrency ceiling; the burst UPDATE/INSERT pair had a documented
reason for one lock and five writers that did not take it; and the patch name
was the last artifact still built from a caller-supplied path fragment.

## Consequences

- **Deployments can split the investigator's credentials now.** Set
  `HOOKPROBE_ACTION_SECRET` on the node that serves cards; the watcher and
  planner need only the event secret. A deployment with neither secret can still
  follow up and rule from a card, but cannot approve — by design.
- **A storm of distinct conditions queues at the judge rather than at the
  provider.** The cap is configurable; the default is a brake on the bill, not
  an attempt to make latency.
- **A session key containing `/`, whitespace or more than 180 safe characters
  gets a digest suffix on its patch filename.** Normal `probe:<source>:<id>`
  keys keep the readable name the board links to; a link to an exotic key
  changes once, and the file itself is what the route reads.
- The judge's write lock now covers every write, so a board read and a
  judgement write can no longer interleave a commit from a third task between
  the burst UPDATE and its INSERT.
