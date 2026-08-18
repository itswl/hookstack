---
title: The probe investigates a burst as one case, not N
status: proposed
date: 2026-08-18
scope: stack
---

## Decision

When the judge tags a burst (different rules, one origin, one window — shipped
2026-08-18), the escalation to the probe should carry the whole burst as ONE
investigation with all the alerts as context, instead of each member firing its
own cold-start run. Not built yet: it needs real burst samples to design the
bundle payload against.

## Why

Coalescing already merges re-fires of the SAME alert into one session. A burst
is the cross-alert case: five different alerts in ten minutes are almost always
one incident with one root cause, and five parallel investigations of it waste
five bills to reach five partial views of the same story — while a human reading
the channel sees five cards and has to assemble the incident themselves.

The judge now assigns a burst_id. The missing pieces:

- the pipe's escalation route would hold a burst briefly (a short debounce) and
  deliver the members together, or deliver the first and let later members
  attach to its probe session (the coalescing machinery already attaches
  re-fires — a burst member is the same move keyed on burst_id instead of
  title);
- the probe's event door would accept a bundle: a primary alert plus the peers
  as context, one session, one report that names the common cause and the
  per-alert impact;
- the report returns once, and the pipe fans it to the members' channels.

## Consequences

- Needs the debounce window tuned against real bursts — too short and members
  miss the bundle, too long and the first responder waits. That is why this
  waits for shadow data rather than shipping a guessed number.
- WebhookWise has incident grouping already; this is the most natural place to
  port a mechanism from WW rather than invent one (see the backport memory).
- Until built, a burst is visible (the judge tags and the board groups it) but
  still investigated per-alert. Visible-but-not-automated is an honest interim.
