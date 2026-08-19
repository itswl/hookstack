---
title: Rewriting hookstack in Go, declined — with the one component that could ever go
status: rejected
date: 2026-08-19
scope: stack
---

## Decision

hookstack stays Python. If any component is ever rewritten in Go it is
hookrelay alone — content-blind, no SDK dependency, smallest semantic surface,
and the one whose open-source peers (Alertmanager, the Prometheus ecosystem)
make Go the native accent. Not now: none of the revisit triggers below hold.

## Why

Measured on the production box on 2026-08-19: relay 45.6MiB RSS / 0.24% CPU,
each judge ~50MiB / 0.2%, images 196MB. Go would take those to ~15MiB and
~15MB — real numbers, and operationally meaningless at 47 judged alerts and
0.2% CPU. The performance argument is empty at this scale and for this shape
of work (the judge waits seconds on an LLM; the relay moves bytes).

hookprobe is the hard constraint: its engine is the Claude Agent SDK
(Python/TS only), and 2026-08-18-pi-as-the-probe-engine already records why
the SDK stays — hooks, skills, MCP and subagents are load-bearing. A Go probe
means hand-rolling the loop, which that note declines. Its 1.21GB image is
mostly the Node CLI and the diagnostic core, which no host language removes.
So "hookstack in Go" can only ever mean "relay and judge in Go, probe in
Python": two toolchains, two gates, two lockfile regimes, one maintainer.

The rewrite would also re-open every trap the ~330 tests fossilise — the
flat-vs-wrapped envelope parse that collapsed identity while looking like
savings, recovery-marker stripping in four languages, storm serialization,
the burst retroactive join — and it runs against the stated strategy that
WebhookWise (Python) is the mainline hookstack feeds mechanisms back into:
today judge/relay code is nearly copy-portable to WW; in Go only the ideas
would travel.

What Go genuinely buys is distribution and ecosystem accent: a single static
binary for SREs who will not run docker, and a codebase the
Prometheus-ecosystem contributor reads as native. Those are positioning
gains, they attach only to the relay, and they matter when adoption is the
bottleneck. Adoption is not the bottleneck; labels and production hours are.

## Consequences

- The wire contracts (PROCESSED-EVENT dialect, the door semantics, the
  timestamped-HMAC signature) stay documented and contract-tested — they are
  the language-independent spec a Go relay would be built against, behind the
  same doors, swappable per deployment.
- Revisit triggers, concretely: sustained throughput that makes asyncio the
  measured bottleneck; a Go-native contributor community forming around the
  project; or single-binary install becoming a demonstrated adoption blocker
  (an issue tracker full of "why docker"). Any one re-opens this for the
  relay only.
- The professionalism question this answered, for the record: in infra OSS
  the signal order is ten-minute demo > release hygiene > visible engineering
  judgment > tests > implementation language. The first four shipped this
  month; language only touches the first via install friction, and only for
  the relay.
