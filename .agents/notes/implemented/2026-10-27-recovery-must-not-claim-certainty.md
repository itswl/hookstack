---
title: Recovery must not claim certainty
status: implemented
date: 2026-10-27
scope: stack
---

## Decision

Record remediation step starts and results durably, and classify interrupted
steps as completed, unknown, or not started. Limit captured output and redact
known secret patterns. Reserve signed inbound requests with renewable SQLite
leases, recording completion only after handling. Treat incomplete posture
measurements and unchecked ambient AWS credentials as unverifiable; a writing
node with a declared radius refuses an unverified measurement under enforce.

## Why

A crash between a side effect and its record cannot establish that it did not
happen. Concurrent requests must not both enter the pipeline while the first is
still processing, and a failed permission check cannot prove a declared radius.

## Consequences

- An unknown remediation step requires reconciliation before another attempt.
- A lease can expire after a crash, allowing retries but not exactly-once external effects.
- Patterns do not catch every secret, and proposals retain executable commands.
- Declared writing nodes may need corrected tooling or an explicit metadata setting before starting.
