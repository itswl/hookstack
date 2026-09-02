---
title: The pipe survives a malformed return, and every writer takes the write lock
status: implemented
date: 2026-09-02
scope: hookrelay
---

## Decision

Three pipe fixes from the review, each small:

- **Poison-pill returns.** `Processed` now coerces `meta`/`analysis`/`identity`
  to `{}` when the wire carries a non-object (`_as_dict`), and `channels.send`
  adds `AttributeError` to the builder-misconfiguration except. A processed
  payload whose `meta` was a string/list used to raise `AttributeError` from an
  accessor, escape `send()`'s narrow except, never dead-letter, and be retried
  every tick forever — head-of-line blocking its channel.
- **Link-label hijack.** `escape_markup` now escapes `]` as well as `[`. A
  supplied link label carrying a close-bracket then a parenthesised URL
  (`"Runbook](https://evil) x"`) closed the markdown link early, so the
  payload's own target became the clickable link.
- **Write-lock coverage.** The ten single-statement writers
  (`mark_sent`, `mark_failed`, `defer_delivery`, `retry_delivery`,
  `mark_escalated`, `spend_action`, `record_action_outcome`, `add_silence`,
  `delete_silence`, `purge_older_than`) now take `_write_lock`, which the store's
  own docstring already claimed "every single-statement writer" does.

## Why

The store shares ONE aiosqlite connection across the ingest tasks, the delivery
worker and the purge, and sqlite's implicit transaction is per-connection. The
docstring on `transaction()` says the lock is why single-statement writers must
take it too — a `commit()` from an unlocked writer landing between two
statements of a `transaction()` commits half a unit, and a `rollback()` in
`transaction()` can undo an unlocked writer's just-committed `UPDATE … sent`
(re-send → duplicate notification). The writers did not take it. This aligns the
code with the invariant the docstring documents.

The poison pill and the label hijack are both reachable from any processed
payload on a return door, and neither had a test.

## Consequences

- A malformed return renders from whatever is left, or fails NAMED into the
  ledger; it can no longer wedge a channel. Verified by new parametrized tests in
  `tests/test_processed_render.py`.
- Writers serialize with `transaction()`. The critical sections are local
  inserts/updates with no network in them, so nothing waits long; verified none
  of the ten is called inside a `transaction()` block, so there is no
  re-entrancy (asyncio.Lock is not reentrant).
- `_announce()` is still called once per writer, outside the lock, unchanged.
