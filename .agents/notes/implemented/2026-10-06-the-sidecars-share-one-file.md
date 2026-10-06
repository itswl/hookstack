---
title: The sidecars share one file
status: implemented
date: 2026-10-06
scope: stack
---

## Decision

The four sidecars — the egress proxy, the MCP gate, the watch signer, the
console ingress — move to `deploy/sidecars/` beside one `common.py`, and each
container mounts that directory read-only instead of its one script. The
shared module holds the plumbing every sidecar has and none of them decides:
the constant-time bearer comparison, the timestamped signature, the ledger,
the byte pump, the threading server, the HTTP handler base with its timeout,
its answer, its refusal and its length check. Each script keeps what it does
— which hosts, which tools, which signals — and its docstring. The tests sit
in one directory and the gate runs one step for all four instead of two.

What stays pinned by `scripts/assert_copies.py` is the two helpers the service
packages also carry (`constant_time_eq`, `sign_timestamped`), because a
container that mounts a directory still cannot import a service package. The
five sidecar-to-sidecar pins and the recorded `IDLE_SECONDS` difference go;
the pump takes its idle limit as a parameter, so the difference between the
proxy's 300 and the ingress's 900 is now an argument, not two constants.

## Why

Third of the four cuts the operator asked for. The copies were the price of
mounting one file per container, and the price had grown to five copies of a
comparison, two ledgers, two pumps and two handlers, kept identical by a
checker — and the review of 2026-10-05 had found two of them already drifted
from the services (the bare `compare_digest` the doors had fixed weeks
earlier). Mounting a directory costs nothing: no image, no build, the same
`python:3.14-slim`, the same edit-and-restart loop.

## Consequences

- Every sidecar container is recreated once (the mount changed). `read_only`
  containers cannot write `__pycache__` into the mount and do not try.
- A new sidecar is a file in this directory and a service block; what it
  shares is one import away, and what it decides stays its own.
- The retired production compose under `hookprobe/deploy/` points at the
  directory too, so it is not left naming a file that no longer exists.
