---
title: Demonstrate the boundary, do not describe it — one endpoint that executes every claim
status: implemented
date: 2026-09-10
scope: hookprobe
---

## Decision

`GET /v1/selftest`. The node performs each boundary it claims and reports what
happened: the gate refuses a tool no posture permits (in process, and again
through the subprocess path), the shell guard refuses a mutation and an egress
bypass, the egress proxy refuses a name nobody listed, the agent's bearer is
refused on a write route, and the credentials are re-measured.

Two rules decide its shape:

* **A check that cannot run reports `held: null` and is listed under
  `unproven` — never a pass.**
* **Every check names what it does not cover**, like each row of
  `docs/containment.md`.

## Why

Asked how to make this safe enough for production, I went and counted what had
actually gone wrong on it in a day. Eight findings, and **not one was a guard
failing**. Every one was a boundary being absent, or a decision being made,
while every surface still read fine:

* a spawned gate that could not import its own package — `kubectl delete` ran
  on a node whose `/v1/agent` said `bash_guard: readonly`;
* an egress allowlist whose bypass was one shell prefix, for the hours between
  shipping the proxy and somebody trying it;
* a price knob no compose could pass, where `.env`, the settings and
  `/v1/budget` all looked right;
* three refusals that reached a ledger and never a person.

The existing surfaces are all descriptions. `/healthz` answers "is the process
up". `/v1/posture` answers "what did the credentials allow **at startup**" — a
credential widened afterwards moves nothing anybody reads. `/v1/agent` answers
"what did the settings ask for". `containment.md` is prose.

So the lever is not another layer. It is making the claims **falsifiable at
runtime**, which this repository already does in two places and never
aggregated: `gate.verify` proves the gate answers before the first turn, and
`gate.consulted` proves it was asked after one. This is that habit, on demand,
for everything.

**`unproven` is the load-bearing part.** A green board assembled out of checks
that quietly skipped is precisely the failure mode above wearing a nicer
jacket. So a laptop with no egress proxy gets `null` and a listing, not a pass —
"has not failed a boundary" and "has one I cannot demonstrate" are different
answers and the endpoint refuses to merge them.

**The claim it cannot make is on the same page.** The audit is append-only JSONL
with no chaining, so *tamper-evident* reports `null` with "not built". That is
the claim a compliance reader most wants; omitting it would have made the report
read complete.

**The egress check takes no host.** An endpoint that opened a connection to a
caller-supplied destination would be a request-forgery hole inside the file
arguing for boundaries. The target is a constant under `.invalid`, reserved by
RFC 2606 so it can never resolve — the refusal is the allowlist's, not DNS's.

## Consequences

* It costs a couple of seconds, because re-measuring the posture shells out to
  `kubectl`/`aws`. On-demand, never on a timer, and it spends no model money.
* **It is not monitoring.** Nothing polls it and nothing alarms on it yet;
  today it answers a person who is deciding whether to trust this container.
  Wiring it to the patrol timer is the obvious next step and deliberately not
  taken in the same change.
* The report is only as honest as its checks. Adding a boundary to
  `containment.md` without adding it here leaves a row that is still only
  prose — and this note is the place that says so.
* Two things this does not do, so nobody reads it as more: it cannot prove a
  boundary held in the PAST (that is the audit's job, and the audit is not yet
  tamper-evident), and it cannot see a runtime that never asks the gate at all
  (that is the per-turn `consulted` check).
