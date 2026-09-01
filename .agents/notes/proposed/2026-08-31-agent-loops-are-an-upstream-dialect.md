---
title: Agent loops are an upstream dialect, and this family monitors them
status: proposed
date: 2026-08-31
scope: stack
---

## Decision

Treat agent-fleet telemetry — hook events from coding agents, automated
approvals, gate verdicts, eval-gate results, memory auto-apply events — as a
first-class upstream: adapter examples in the pipe, judge verdicts answering
"does a person need to see this agent behaviour now", probe investigations
answering "why did the agent do that". Dogfood first: hookstack's own gate
runs, deploy-eval results and auto-apply events post to the family's own
front door before any of this is offered outward.

## Why

Anthropic's security post describes the shift in one line: their engineers
stopped monitoring bugs and started monitoring loops, with agents treated as
a new class of insider — baselined, anomaly-detected, audited. The monitoring
stacks this family already adapts (Grafana, Alertmanager, cloud monitors) do
not carry that traffic, and nothing else in the market does either yet.

The pipe is content-blind and adapts dialects declaratively, so a new event
class should be config plus one example, not a service. The judge's two axes
are already the right questions for this traffic: an agent proposing ten
times its baseline is important AND worth interrupting someone for; a gate
re-run that went green is neither.

## Consequences

- Expected to need example adapter configs and one patrol brief, no service
  code — which is also the test: if it turns out to need code in the pipe,
  the doctrine question ("a property of a good pipe, or a judgment?") gets
  asked before a line is written.
- Agent telemetry carries repo paths, hostnames and rule names. The estate
  guard reads tracked files only, so the scrub burden lands on the adapter
  examples and docs — the same posture skills export takes.
- New event class, no goldens: before any judge-prompt change is made FOR
  agent events, the eval set needs reviewed rows for them, or the deploy gate
  is blind to regressions there.
- Baselines ("ten times its normal proposal rate") wait for real volume from
  the dogfood. The burst note took the same measure-before-mechanism path and
  it held.

## Addendum (2026-08-31, same day): the dogfood slice shipped

The adapter example (`hookrelay/examples/agent-loops.yaml`, pinned by
`tests/test_agent_loop_example.py`) and the patrol brief
(`hookprobe/examples/patrols/weekly-loop-review.md`) exist as of this
afternoon. Two wiring facts the design absorbed on contact:

- The judge's `/status?q=` matches title and summary only — not source — so
  the adapter prefixes every title with `agent-loop`. The prefix is
  load-bearing: it is what makes the loop family queryable as one, and the
  test pins it.
- `fields.alertname={loop}` makes each loop a RULE, so a nightly green gate
  answers from the reuse routes instead of buying a verdict, and
  `fields.origin` lets one origin's simultaneous failures wear one burst id.

Still open, deliberately: senders beyond a documented curl (crontab and CI
lines stay the operator's), baselines (wait for volume), the probe escalation
wire (only once the investigator has tools that can see a loop), anything
offered outward.
