---
title: The audit says when it has been edited — a chain, not a ledger
status: implemented
date: 2026-09-10
scope: hookprobe
---

## Decision

Every flight-recorder line carries the hash of the line before it.
`gate.verify_chain` walks the audit in order and names the first line that stops
adding up; `/v1/selftest` runs it, and the row that reported `null — not built`
from the day that endpoint shipped now reports a number.

Not a distributed ledger, not a blockchain. A hash chain plus an off-box copy.

## Why

The audit is what an investigation gets reconstructed from, what
`gate.output_secrets` reads to say a credential reached a model's context, and
the thing a compliance reader asks about first. It was append-only JSONL: **a
line rewritten later was indistinguishable from one written that way.**

The blueprint that prompted this asked for "分布式只读账本". That answers a
question nobody here is asking — the threat is somebody quietly tidying one
record on this disk, not a consortium disagreeing about history. A chain answers
that; the rest is cost.

Three design points, each because the obvious version is wrong:

* **Each line names its predecessor, rather than only hashing itself.** Content
  hashes catch an edit and miss a deletion. The link is what makes a removed or
  reordered line show.
* **The digest is over canonical JSON, not the bytes on disk.** A reader that
  re-serialises differently still verifies, because the record is the facts, not
  the formatting.
* **Unchained lines are counted, never treated as a break.** Every existing
  deployment has history from before this. An alarm that fired on all of it on
  day one would be ignored by day two. An unchained line appearing *after* the
  chain has started is a break, because that is what stripping the links off a
  record looks like.

**The write never loses a line.** If the chain cannot be kept — a locked file, a
read-only mount — the line is written unchained and verification reports the
gap. A missing audit line is worse than an unverifiable one: the second can be
reported, the first leaves nothing to report. That ordering is stated as a test.

Writing is serialised with a lock on a chain file rather than the day file: the
day file rolls over at midnight and the chain does not, and the gate is
**spawned per tool call**, so two writers racing on the same predecessor is the
ordinary case rather than the rare one.

## Consequences

* Twenty-fourth containment boundary, and it closes the one `unproven` row the
  selftest shipped with. That row existing is why this got built — the gap was
  on the same page as the boundaries that held.
* **The off-box half shipped the same day.** The gap this note first recorded —
  an edit by whoever can also rewrite `.chain` — is now bounded: every report
  carries `meta.audit_head` home, the pipe keeps it in its own ledger on its own
  disk, and `gate.chain_anchored` asks whether any line here still hashes to a
  head somebody else wrote down. A test proves the limit it closes: a
  perfectly rebuilt chain passes `verify_chain` and fails the anchor.

  What remains is an editor who can reach both disks. Two services is the bound
  this buys, and calling it proof would be the overstatement this file keeps
  correcting.
* Verification walks the last 7 day-files by default. The whole history is the
  honest answer and an unbounded read on a path the selftest calls is not; the
  caller can ask for more.
* A selftest run now writes to the audit — its gate probe is a real refusal and
  is recorded as one, under `probe:selftest:0` rather than a real run's key, so
  it cannot inflate the per-session refusal count that exists to spot a run
  which kept asking for what it may not have. Found by a test that assumed
  otherwise.
