---
title: Codex drove a real node end to end — five obligations held, and a budget ceiling that cannot bind
status: proposed
date: 2026-09-09
scope: hookprobe
---

## Decision

Nothing was built for this. A temporary node was run on `HOOKPROBE_RUNTIME=codex`
against this deployment's own gateway, on its own port and workdir, and torn
down; the three existing nodes were not touched. It is recorded because a
contract satisfied by tests and a contract satisfied by a running node are
different claims, and because the run produced one finding no test would have.

## What held, measured rather than argued

`/v1/agent` reported `adapter: codex` — the first time any running node in this
repository has reported a runtime other than Claude. Then one turn, asked to run
one permitted command and one the posture forbids:

```
First command printed: `conformance-ok`
Second command was blocked.
Exact reason: `read-only guard: kubectl mutation or pod entry is blocked;
               this runner may only observe, never change.`
```

The five obligations, from the run record and the flight recorder:

* **Gate before the tool** — the refusal above, through a spawned
  `python -m hookprobe.gate`, in a real turn on a real model.
* **An audit the agent cannot edit** — three lines: the startup self-test
  (`probe:gate-selftest`, `denied: true`), the permitted call
  (`error: false`), and the refused one (`denied: true`, `guard: bash`, with
  the reason). A refusal and a failure are different shapes, both greppable.
* **Session identity** — `engine_session_id 01a08418-…`, recorded and resumable.
* **Incremental events** — the follow-up turn continued the same thread.
* **Cost, or None** — `cost_usd: None` on every turn. Codex reports tokens and
  never money, and the ledger recorded that as unpriced rather than as free.

And today's secrets fix, verified where it matters rather than in a unit test:
the agent was asked for the LENGTHS of three withheld variables and its own
provider token, and answered `lens 0 0 0 51`. The three the service withholds
are empty inside a real codex subprocess; the provider credential it needs is
intact.

Two configuration facts worth having: codex-cli 0.153.4 refuses
`wire_api = "chat"` outright and requires `"responses"`, and this deployment's
gateway does serve `/v1/responses`. The first attempt failed on that, and the
failure came back as a proper report with `confidence 0.0` — the failure path
behaving as designed.

## Why the finding matters more than the run

**A budget ceiling does not bind on a codex node, and the node displays one
anyway.** After three turns and **93,388 input tokens**, `/v1/budget` read:

```
budget_usd 1.0 · spent_usd 0.0 · remaining_usd 1.0 · exhausted false
unpriced_turns 3 · investigations 2
```

Every figure there is honest on its own. `window_spend()` sums priced cost, the
breaker compares that to the ceiling, and codex prices nothing — so the sum is
0.0 for any amount of real spend. `HOOKPROBE_BUDGET_USD` on a codex node is
decorative: it cannot trip, ever, and the surface that shows it says
`remaining_usd 1.0` while the node spends.

**The fix is not a price table.** That is the same mistake as the weekly page
calling a locally-priced number "billed" — an invented figure that a breaker
would then act on with false confidence. The honest change is for the ceiling to
say it cannot bind when the window's turns are unpriced, rather than reporting
headroom it cannot measure. `unpriced_turns` is already there and already the
right signal; nothing reads it as a qualifier on `remaining_usd`.

## Consequences

**An operator can set a budget on a codex node and be wrong about what it
does.** Until the surface says otherwise, the only runtimes whose spend the
breaker can see are `claude` (a price table for a model it is not billing —
see the priced-not-billed note) and `pi` (when the model is in its catalogue).
On codex the answer is not wrong, it is absent, which is quieter.

**The three deployed nodes were not touched and nothing was left running.** The
temporary node's workdir and its provider config — which held a real gateway
credential and lived in `/tmp`, never in the repository — were removed.
