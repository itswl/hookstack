---
title: pi as the third runtime — where two runtimes stop agreeing, and a reported cost that was worse than none
status: proposed
date: 2026-09-09
scope: hookprobe
---

## Decision

`HOOKPROBE_RUNTIME=pi` drives the pi CLI. Its gate is `pi_gate.ts`, shipped in
the package, holding no policy: it translates pi's lower-case tool names into
the ones the guards speak, calls `python -m hookprobe.gate`, and turns the one
answer into pi's `{ block, reason }`.

Codex proved the contract could be met twice. pi is what made writing it
worthwhile, because pi is where the runtimes stop agreeing.

## The zero that lied

Codex reports tokens and never money, so `cost_usd` is `None` and the honesty is
free. **pi reports cost**, priced from its own model catalog — and this
deployment's model is served through a private gateway that is not in that
catalog. So the first real turn came back with

```
usage.totalTokens = 6397
usage.cost.total  = 0
```

which is precisely the shape the contract forbids and the reason the obligation
is worded "cost, or `None`, but never 0.0". A budget breaker fed those numbers
would watch an unattended node spend all week and see nothing at all — worse
than a runtime that admits it cannot count, because nothing would ever look
wrong.

So a zero with tokens behind it is reported as `None`, and only a zero with
nothing spent is allowed to mean free. When the model IS in pi's catalog the
real figure passes through, which makes pi the only non-Claude runtime here
whose spend the breaker can see.

**The general lesson is not about pi.** A runtime that reports a number is more
dangerous than one that reports nothing, because the number arrives with the
same confidence whether or not anyone counted. The next adapter's author should
assume a price of zero is unpriced until they have seen it be right.

## Where the two disagree, and why the feed still reads the same

**A refused call.** Codex omits the tool entirely — no `command_execution` item
at all. pi announces `tool_execution_start`, preflights, and ends the call with
the refusal as its result and `isError: true`. Both honest. The adapter marks
the step rather than hiding it, which is arguably the better record: it shows
what the agent tried. An adapter written from the Protocol's signatures alone
would have reported a refused call as a successful one.

**Where the gate lives.** Codex spawns a command it reads on stdout; pi loads a
TypeScript module in-process. Neither difference reaches `gate.py`, which is the
point — `gate.environment()` and `gate.verify()` are now shared, so the second
and third adapters do not each decide separately what the gate may know.

**No sandbox at all.** Codex has `--sandbox read-only`, a kernel-level refusal
under the guard. pi documents that it has none: built-in tools run with the
process's permissions. On pi the extension IS the whole posture, so a missing
extension stops the node rather than degrading it.

## What nearly shipped broken

`pi_gate.ts` was not in `package-data`, which listed only `*.html`. An installed
image would have had the adapter and not the posture it depends on. The node
would have refused to start — the failure mode is right — but a packaging list
is a poor way to find that out, so the conformance suite now asserts the
extension ships as well as existing.

## Consequences

**Three adapters, one posture, one suite.** `runtimes.py` is the registry the
suite reads, so a fourth cannot be added without being judged.

**Proven live, both of them.** Same drill each time: ask for a command the guard
refuses and one it permits, then resume the session from a separate process. On
pi the refused `kubectl delete` was blocked with the reason reaching the model,
the audit carried three lines including the refusal and the gate's own
self-test, and a second process resumed the session and recalled what the first
one had been told.

**Still deployed nowhere.** No image ships either CLI and no deployment sets
`HOOKPROBE_RUNTIME`. See [[2026-09-09-the-second-runtime-adapter-codex]] for
what running production on one would cost.
