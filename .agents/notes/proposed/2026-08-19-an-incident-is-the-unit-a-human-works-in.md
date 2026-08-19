---
title: An incident is the unit a human works in, and nothing in the family models one
status: proposed
date: 2026-08-19
scope: stack
---

## Decision

Give the family a first-class **incident**: N alerts, the verdicts they were
given, the investigations they funded, what a person did about it, and when it
ended. Not built. The pieces to build it from already exist and are listed
below, which is why this is worth writing down rather than leaving as a feeling
that the boards are hard to read.

## Why

Every component models its own unit and none of them is the operator's:

- hookrelay keys everything to an **event** (one inbound payload).
- hookjudge keys to a **judgement**, with `burst_id` grouping different rules
  from one origin inside a window (shipped 2026-08-18).
- hookprobe keys to a **session**, and coalesces re-fires of the same alert.

An operator's unit is none of these. At 09:00 the question is "what happened
last night" — and the answer spans five alerts, two verdicts, one investigation
and a silence somebody pressed at 02:40. Today that reconstruction is manual and
crosses three ports with three tokens.

Two things make it newly worth doing. First, `GET /trace/{event_id}` already
assembles most of one alert's story — the payload as received, every delivery
with its bytes, what each brain returned, and now the human's button presses
(`human_actions`, 2026-08-19). It is an incident timeline for N=1. Second, the
attention numbers hookjudge now reports (`interruptions`, `repeats`) are
per-condition, and the number an operator actually wants to drive down is
**interruptions per incident** — five cards for one root cause is the noise, and
no component can currently see that it was one root cause.

## Consequences

- The natural home is the pipe: it is the only component that sees every event,
  every verdict returning, every investigation returning, and every button
  press. It is also the component that must stay content-blind, so an incident
  there can only be a grouping of correlation ids and timestamps — the *meaning*
  (these four alerts are one incident) has to arrive from the judge's
  `burst_id`, which today does not travel back to the pipe.
- So the smallest real step is not a new object: it is carrying `burst_id`
  through the processed-event contract, and letting `/trace` group by it. That
  is a contract change plus a query, and it would make the morning review
  possible without anything calling itself an incident.
- What it unlocks beyond the review: a postmortem export, and the honest version
  of the noise metric. What it costs: the pipe learns one more thing about
  content, which is the boundary this family is careful about.
- Do not model an incident in three places. If each service grows its own, the
  three will disagree about what one incident is, and reconciling them is worse
  than not having it.

## Rejected

- **An incident object in each service, reconciled at read time.** Three
  definitions of one incident, and the reconciliation lives nowhere.
- **Inferring incidents in the pipe from timing alone.** The pipe is
  content-blind by design; "these arrived close together" is exactly the
  heuristic the judge's `burst_id` was built to replace.
