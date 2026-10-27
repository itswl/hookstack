---
title: The lease lets the request finish, a replay of a handled request is a read, and the lease length is a setting
status: implemented
date: 2026-10-27
scope: hookrelay
---

## Decision

Three follow-ups to "Recovery must not claim certainty", named in its review.

1. **A failed renewal no longer cancels the request.** The heartbeat logs and
   keeps trying; `complete_signature` decides at the end: a lease still the
   request's completes it, a lease lost answers 503.
2. **A replay of a handled request is answered from a read.** The spent row is
   looked up on the read connection before the write lock is taken; only a
   signature not yet spent takes the write path. The storm fuse is unchanged.
3. **The lease length is `HOOKRELAY_SIGNATURE_LEASE_SECONDS`**, 120 by default
   and at least 10, renewed every third of it.

## Why

Cancelling mid-pipeline left the sender a dropped connection instead of an
answer and the pipeline half-run, while the completion check already re-asks
whether the lease is still the request's. Every copy of a bulk replay took the
write lock, where the door before the lease answered it from a read. A fixed
120 seconds could not follow a pipeline slower or faster than the one it was
measured on.

## Consequences

- A request whose renewals fail still completes while its lease is unexpired;
  one that outlives a lost lease answers 503, and the sender's retry decides.
- A replay storm of one capture costs reads, not writes.
- The pipe's ceiling moves 6,650 to 6,700, argued in `scripts/assert_weight.py`.
