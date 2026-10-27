---
title: Recovery must not claim certainty
status: implemented
date: 2026-10-27
scope: stack
---

## Decision

Record remediation step starts and results durably, and classify interrupted
steps as completed, unknown, or not started. Redact a step's whole output, then
keep its last 10,000 characters (omit it past a megabyte). Reserve signed
inbound requests with renewable SQLite leases, recording completion only after
handling. Treat incomplete posture measurements as unverifiable, and ASK the
instance metadata service, as the AWS CLI does, when nothing names an AWS
credential: a role it offers is measured; no role, another vendor's service or
nothing there is no credential. A writing node with a declared radius refuses
an unverified measurement under enforce.

Amended in review before merge: the first version assumed metadata credentials
whenever nothing named one, which read every laptop and every probe on an
internal network as `unverifiable` and stopped a declared writing node from
starting; it omitted any output past 10,000 bytes, tail included; and it cut
the pipe's explanatory comments to fit its line ceiling. The pipe's two unused
signature methods went instead.

## Why

A crash between a side effect and its record cannot establish that it did not
happen. Concurrent requests must not both enter the pipeline while the first is
still processing, and a failed permission check cannot prove a declared radius.

## Consequences

- An unknown remediation step requires reconciliation before another attempt.
- A lease can expire after a crash, allowing retries but not exactly-once external effects.
- Patterns do not catch every secret, and proposals retain executable commands.
- Declared writing nodes may need corrected tooling or an explicit metadata setting before starting.
- The pipe gains a `pending_signatures` table. A rollback to an earlier pipe
  ignores it: nothing fails to open, and a lease left in it simply expires.
