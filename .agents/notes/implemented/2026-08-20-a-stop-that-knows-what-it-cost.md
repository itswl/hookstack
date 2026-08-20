---
title: The engine is interrupted, not killed — a stop that still knows its bill
status: implemented
date: 2026-08-20
scope: hookprobe
---

## Decision

The engine drives the SDK through `ClaudeSDKClient` and ends a turn with
`interrupt()`, instead of running `query()` and cancelling the coroutine from
outside. All three paths that end a turn early now ask before they kill:

| path | how often | before | now |
| --- | --- | --- | --- |
| operator presses Stop | whenever somebody watches a run | `task.cancel()` | interrupt, cancel after 10s |
| wall-clock timeout | unknown until real traffic | `wait_for` cancel | interrupt, cancel after 15s |
| deploy restart | **every deploy** | `task.cancel()` | interrupt, then the existing grace |

Cancellation remains the fallback in all three, because an interrupt is a
request: a turn that has not reached the SDK yet has nothing to interrupt, and
one the SDK ignores must not run forever because we asked politely.

## Why

The SDK reports dollars only on its final `ResultMessage`. `query()` is a
one-shot async generator, so the only way to stop it early was to cancel the
coroutine — which threw that message away. The turn then recorded `cost_usd`
`None`, meaning *nobody counted*, for a run the provider had already billed in
full.

The measurement that changed the decision was not about timeouts. An earlier
pass deferred this work on the grounds that timeouts are probably rare and the
undercount was at least visible (`unpriced_turns`). That reasoning rested on a
false premise: reading the code showed `task.cancel()` on **three** paths, and
two of them are routine. The operator's Stop button is a feature people press on
purpose. A deploy restart happens every release, and loses one cost per
investigation in flight. Neither needs traffic data to establish its frequency.

Verified against the live SDK, not only against fakes:

- a normal run through the new client path returned `cost_usd 0.208`
- a run interrupted six seconds in settled with `cost_usd 0.00097` and
  `error: engine reported error_during_execution` — the bill arrived, and the
  turn did not read as an answer

## Consequences

- An interrupted timeout keeps its verdict. The SDK can report a clean finish
  for a turn we cut off, and letting that read as success would put a truncated
  report where an answer belongs — so the timeout error is preserved and the
  cost is added: `timed out after Ns (interrupted; cost recorded)`.
- `unpriced_turns` still exists and now measures the residue rather than the
  common case: turns the interrupt could not save. A flat zero on that series is
  the feature working, which is why the metric test asserts zero where it used
  to assert one.
- The boundary that had no test coverage now has some. `tests/helpers.py`'s fake
  engines model the interrupt protocol — an interruptible sleep that returns a
  bill, plus a `stoppable = False` switch that plays an SDK ignoring the ask — so
  the Stop path, the fallback path and the restart path are all covered. A fake
  that merely returned `True` would have proved nothing.
- `disconnect()` is now the engine's responsibility in a `finally`; a client left
  open holds the CLI subprocess.
- Stop answers the operator immediately and arranges the wind-down in the
  background. Waiting to see which path won would have made the button feel
  broken for ten seconds.

## Rejected

- **Waiting for production data first.** The position of the earlier pass, and
  correct given what it believed. It was arguing about timeout frequency while
  two routine paths lost the same number.
- **Interrupt with no cancel fallback.** An interrupt the SDK ignores would
  strand a turn forever, and the Stop button has to be able to stop things.
- **Cancelling immediately on timeout and accepting the gap.** That is the old
  behaviour with extra steps: the grace period is precisely what buys the
  accounting, because winding down means finishing the current step and emitting
  the final message.
