---
title: Three copies of live.py, the alarm and the HMAC — measured, not yet merged
status: proposed
date: 2026-08-19
scope: stack
---

## Decision

Leave the duplication in place for now, and record what it actually costs so
the next person is deciding rather than discovering. A full audit on
2026-08-19 found three pieces of code living in every service at once:

- `live.py` — **byte-identical** in all three (md5 `dee09814…`, 90 lines each).
- `alarm.py` — a near-duplicate in hookrelay (50 lines) and hookjudge (47):
  same `SelfAlarm`, same three disciplines, differing only in method name
  (`dead_letter` / `dead_return`), message text and docstring wording.
- The timestamped-HMAC verify — three homes: `hookrelay/security.py`,
  `hookjudge/app.py`, `hookprobe/wire.py`.

Merging them means a fourth installable thing (a `hookstack_wire` package, or
vendoring), and every Dockerfile currently copies exactly one package
directory. That is a deployment change, not a refactor, which is why it gets a
note instead of a commit.

## Why

The duplication is not free, and the audit has the receipts: the same
`hmac.compare_digest` defect existed in **five** places at once — all three
services plus the GitHub example plugin — because the comparison was written
five times. One non-ASCII byte in any credential header answered HTTP 500
instead of 401, unauthenticated and remote. Fixing it meant five edits that
had to agree, and nothing in the repository would have noticed if one had been
missed. The HMAC copies had already drifted in posture, too: hookrelay can
refuse the replayable body-only form per door (`require_timestamp`), hookjudge
cannot and hardcodes its 300-second window.

Against that, the reason the copies exist is real and is stated in the
README: each service is self-contained — its own package, tests, gate,
Dockerfile and CI — so a change to one cannot queue the other's jobs or break
the other's deploy. `2026-08-18-the-root-gate-delegates-it-never-replicates`
argues the same shape from the other side: one source of truth per component,
and a second copy that no test pins is what drifts silently.

So the tension is genuine, and it is not resolved by preferring one principle
in the abstract. What tips it, when it tips, is a defect class that lands in N
places at once. That has now happened once.

## Consequences

- Whoever merges these owns a packaging decision: a shared distribution
  installed by three Dockerfiles, or a vendor step with a check that the
  copies match. The second keeps deployment independence and turns drift into
  a gate failure, which is the cheaper half of the benefit.
- The narrowest useful scope is the wire: signature verification and the
  constant-time comparison. It is the piece where a divergence is a security
  bug rather than a cosmetic one, and it is ~40 lines.
- `live.py` being byte-identical makes it the safest candidate and the least
  valuable one: a board that drifts is visible immediately, and
  `scripts/assert_design.py` already pins the pages that consume it.
- Until then: a fix to any of these three must be applied everywhere in the
  same change. The compare_digest fix of 2026-08-19 is the worked example —
  five sites, one commit.

## Rejected

- **Merging all three now, in the same change as the audit's bug fixes.**
  Mixing a deployment change into a security fix means the fix cannot be
  reviewed on its own or reverted on its own.
- **Leaving it unrecorded.** The duplication reads as an accident and keeps
  being rediscovered; the point of this bucket is that the next reader inherits
  the measurement instead of repeating it.
