---
title: A second runtime adapter — deferred by decision, and what it has to prove
status: proposed
date: 2026-09-08
scope: hookprobe
---

## Decision

Not built, and deliberately last. The operator deferred it on 2026-09-08 after
four rounds of work that each had production evidence behind them; this one has
none, only the PRD's position that hookstack should not hard-depend on a single
agent runtime. Parking it is the right call and this note is the parking space,
so the next person to reach for it finds the requirements rather than
re-deriving them.

## Why it is worth doing eventually

`service.Engine` is a Protocol with one implementation. A contract satisfied
once is a description of that implementation, not a contract, and nothing in
the test suite would notice if a second adapter met every signature and none of
the obligations. The product claim it protects is not portability for its own
sake — it is that the containment story survives a runtime swap.

## What a candidate must supply, before any code is written

Two of these are invisible in the Protocol's signatures, which is why they are
listed first and why the requirement is written on the type itself:

1. **A tool gate that runs BEFORE a tool does.** The read-only posture is a
   PreToolUse hook that refuses mutating verbs, and the startup posture check
   measures credentials against what the node declared. A runtime that can only
   be *asked* not to write cannot run under `readonly`; adopting one would keep
   the word and lose the boundary.
2. **A per-call audit record the agent cannot edit**, including inside
   subagents, whose calls never appear in the message stream. Without it a run's
   account of itself is the run's own word.
3. **Session identity that outlives the process**, because `recover_orphans`
   resumes a run a crash interrupted, possibly from a previous boot.
4. **An incremental event stream** carrying `{"type": "session", "id": …}` as
   soon as the id is known — emitting it only with the result is what made an
   interrupted first turn unrecoverable.
5. **Cost and usage, or `None`** — never `0.0` as a stand-in for unknown. The
   ledger keeps "nobody counted" and "this was free" apart and the budget
   breaker depends on it.

**If a candidate cannot supply 1 and 2, that is the finding, not an obstacle to
work around.** The honest outcome would be to say so: this stack runs agents
under a gate, and a runtime without one is a different product.

## What to build when it is taken up

A conformance suite before an adapter — the shape
`deploy/lark-bridge/tests/test_protocol.py` already uses for the chat bridge,
where one fixture drives any implementation and the second implementer copies
the file. Written against `ClaudeAgentEngine` first, so it is known to pass
something before it is used to judge anything.

## Consequences of leaving it

The Runtime Contract stays written and unproven. Everything that depends on it
— resume across a restart, the audit record, the posture claims — is exercised
against one adapter only, and a regression in the *contract's meaning* (as
opposed to the adapter's behaviour) has nothing watching it. That is the cost of
deferring, and it is smaller than building an adapter for a runtime nobody has
asked to run.
