---
title: The chat's tools are on a list in another container
status: implemented
date: 2026-09-30
scope: stack
---

## Decision

The work deployment's three probes lose their route off the container network,
and the chat client's MCP tools are reached through a gate that holds the only
way to it.

- `deploy/docker-compose.work.yml` gains `probe_net`, `internal: true`.
  `probe-plan`, `probe-watch` and `probe-work` are on it and nothing else: no
  host, no internet, no DNS for either.
- `deploy/mcp-gate/gate.py` sits on both networks. One token per node, a list of
  tool-name globs per token, and a `tools/call` for anything else is refused with
  a JSON-RPC error and recorded. `tools/list` answers are filtered to the same
  list, in JSON and in SSE. A refused request takes its whole batch with it.
- `deploy/probe-ingress/forward.py` restores the three consoles at their old
  addresses. An internal network answers no published port, measured before this
  was written rather than after.
- The egress proxy and the pipe join `probe_net` as well; the probes reach them
  by name exactly as before.

## Why

The investigator's MCP allowlist (`HOOKPROBE_MCP_TOOLS`) is enforced where the
agent asks for a TOOL. It says nothing about a socket. Measured on 2026-09-30
from inside `probe-watch`:

- one plain HTTP POST reached the chat client's own MCP port, with no credential
  of any kind;
- the server listed 34 tools, 20 of them writes, including sending a message as
  the operator, creating a group and uploading a file;
- a tool the node's allowlist does not name was called and answered.

The read-only bash guard does not refuse that request, and should not: refusing
"a POST to a port" is not what it is about. `probe-watch` reads colleagues'
messages for a living, which is this family's own definition of
attacker-influenced text, so the distance between an injected instruction and a
message posted as the operator was one `curl`.

A gate alone would have been theatre. The port had to become unreachable, and on
this host only the network can do that.

## Consequences

- The watcher sees 8 tools where the server offers 34; the planner 11. Both
  lists are exactly what those nodes were already allowed, so nothing they
  legitimately did changed.
- A tool a node needs is now two edits: its own `HOOKPROBE_MCP_TOOLS` and the
  gate's list for that node. That is the price of the list being in a place.
- The probes' published ports are gone. Anything that dialled `127.0.0.1:8088`
  still works, through `probe-ingress`.
- `work-data/mcp-gate/calls.jsonl` is the first place that can answer "did a run
  ask for a tool nobody gave it".
- What this does NOT stop is in `docs/containment.md` and in the module
  docstring: arguments are not checked, a permitted read still reaches the model
  provider, and the client token lives in the agent's own MCP config by
  construction.
