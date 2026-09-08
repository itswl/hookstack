---
title: A restart continues an investigation instead of burying it — stage two, first half
status: proposed
date: 2026-09-08
scope: hookprobe
---

## Decision

Built: a run left mid-flight by a restart is **continued in its own engine
session** rather than settled as a failure, bounded four ways; the engine
session id is recorded **mid-turn** so a first turn is recoverable at all; a
failed run can be retried by a person from the board; and the runtime contract
is written down where the type is, including the two obligations that are
invisible in its signatures.

Not built, again on purpose: a Runtime Contract *refactor*. The `Engine`
Protocol already is the contract, and it now says what it means. Rewriting it
into an abstract base with one implementation and no second consumer would be
structure bought on speculation, which this repository spends its checks
preventing.

## Why

**The transcript outlives the process; the handle to it did not.** The engine
keeps its session on the data volume, so everything an interrupted attempt
gathered — tool output, evidence, dead ends — survives a restart. What did not
survive was the id: it was read off the engine's *result*, so a turn killed
before it finished left a run with no way to continue. The service then failed
the run, reported the failure, and an operator re-asked the question by hand and
paid for the whole investigation twice. The runtime now publishes the id as an
event the moment it first says it and the service checkpoints it immediately, so
the case worth recovering — a first turn cut off partway — is the case that now
recovers.

**Four bounds, because this is the only path that spends money with nobody
asking.** A session id must exist. One resume per run, counted in `meta.resumes`
and persisted, so a process that dies on every boot buys one continuation rather
than one per restart forever. The budget breaker, checked here as the event door
checks it. And `HOOKPROBE_RESUME_INTERRUPTED=off` for a deployment that wants no
restart ever spending on its own. Each has a test; the bounds matter more than
the feature.

**The lost attempt is a turn priced `null`.** Not dropped, not recorded as free.
The provider billed whatever it billed and no result ever came back to say, and
the ledger already keeps "nobody counted" and "this was free" apart — this is
exactly the case `window_unpriced()` was built to count.

**Retry is not budget-gated.** The same position `/hooks/agent` takes and for
the same reason: a human's explicit request should not bounce off a meter. It is
also uncapped, because pressing it again is a person's decision, and every press
lands in the record with the name of whoever asked.

**The contract's two invisible obligations.** A tool gate that runs before a
tool does, and a per-call audit record the agent cannot edit. Both come from the
CLI's hook mechanism today, both are how the read-only posture and
`/v1/runs/{key}/audit` mean anything, and neither appears in `run()`,
`stop()` or `describe_inputs()`. A second adapter that satisfies the signatures
without them would keep every word in the documentation and lose the boundary
behind it — which is why the requirement is written where the type is rather
than in a design document nobody opens while implementing.

## Consequences

**A restart can now cost money.** Bounded, off-switchable, and cheaper than the
alternative it replaces, but it is a real change to what an unattended service
does on its own and it should be read that way. The figure to watch is
`meta.resumes` against the window's spend.

**A resumed run has a turn with no text in it.** The console shows the
interrupted attempt as its own turn, which reads oddly until you know what it
is; it is the honest shape and the operating guide explains it.

**`sweep_orphans` is gone**, replaced by `recover_orphans` returning
`(resumed, failed)`. Anything quoting the old name is stale.

**Stage two's remaining half is a second runtime.** Nothing here proves the
contract by satisfying it twice, and a contract with one implementation is a
description. The first honest test of it is an adapter that has to supply its
own tool gate and audit hook — and if a candidate runtime cannot, that is the
finding, not a detail to work around.
