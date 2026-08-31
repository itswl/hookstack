---
title: An approved remediation proposal exports as an intent artifact
status: proposed
date: 2026-08-31
scope: hookprobe
---

## Decision

A remediation proposal (and, behind it, the investigation that produced it)
gains an export: one artifact in the playbook's intent shape — context,
evidence, proposed change, constraints, provenance — cut and scrubbed by the
same machinery as `/v1/skills/export`, for whatever coding agent the operator
runs. The probe still never applies anything; the artifact is a brief, not a
change.

## Why

Anthropic's playbook closes the Maintain stage by handing monitoring's
diagnosis back to Plan as a new intent, and that is exactly where this
family's loop currently stops: at a card with an approve button. The
diagnosis is paid for, then retyped by whoever fixes the thing. Exporting it
keeps the family's whole posture — propose-not-act, scrub on the way out,
`review` reported and never silently removed — while placing the family
UPSTREAM of coding agents rather than in competition with them: platform
tools decide who fixes; this decides what is worth fixing and carries the
evidence.

## Consequences

- The export inherits the skills-export rules wholesale: cases and
  identifiers do not travel, `review` reports rather than removes, and
  `must_read` is emitted whether or not a pattern matched.
- Start as a GET on the proposal (pull). A POST that pushes the artifact into
  a repo is an outward write and earns its own decision when someone actually
  needs it — not before.
- Idempotent per proposal, like every other door on this service.
- The artifact must name its provenance (session key, correlation id,
  approval actor, at) or it degrades into prose nobody can audit — the same
  rule the eval corpus states for label provenance.
