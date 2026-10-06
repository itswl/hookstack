---
title: Three doors, one container
status: implemented
date: 2026-10-06
scope: stack
---

## Decision

The egress proxy, the MCP gate and the console ingress run in one container,
`doors`, from one entrypoint (`deploy/sidecars/doors.py`) that binds all three
before any serves. Each module keeps its own file, its own docstring and its
own `main()`, and gains a `build()` that returns the bound server. The
container carries the three old service names as network aliases on both
networks, so the probes' proxy variables, their MCP config on the volume and
every document that names `egress-proxy`, `mcp-gate` or `probe-ingress` still
resolve. The watch signer is not merged: it holds the door's secret on the
probes' network only, and a process that also carried the proxy's reach to the
host would carry it for the secret too.

## Why

The operator asked whether ten containers were too many. Seven are the
boundaries themselves — three probes with their own credentials, the signer,
the pipe, the bridge, the timer outside the node it judges. The three doors
were the one case where three containers were three listeners: all stateless,
all on both networks, already sharing one module. One process costs nothing a
boundary paid for.

## Consequences

- One failure takes the three doors down together; they are stateless and the
  restart policy brings them back as one.
- `read_only` root with the gate's ledger on its own volume; `mem_limit`
  raised to 256m for three servers.
- Logs carry the logger's name (`egress`, `mcp-gate`, `ingress`) instead of a
  per-file literal, so three servers' lines in one stream say which door.
- Deploying it: stop and remove the three old containers, then `up -d doors`;
  there is a gap of seconds in which a probe's outbound call or chat tool call
  fails, so do it between ticks.
