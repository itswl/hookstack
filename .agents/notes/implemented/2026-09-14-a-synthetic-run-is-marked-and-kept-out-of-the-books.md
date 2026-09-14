---
title: A synthetic run is marked once and kept out of the books everywhere
status: implemented
date: 2026-09-14
scope: hookprobe
---

## Decision

A run whose session key starts with one of `HOOKPROBE_SYNTHETIC_KEY_PREFIXES`
(default `manual:,drill:`) carries `meta.synthetic = true` from the moment it is
created. Every reader that turns runs into knowledge or numbers leaves it out:
the distiller writes no runbook from it, the re-fire gate never anchors on it,
`_vouching_run` never counts its ruling, `work.resolve` folds it out with the
notices, and `cost_report.py` drops it from the week. The run summary says
`synthetic: true` so any other reader can do the same.

## Why

Three findings from one day on the production deployment, each a different
reader failing the same way.

The runbook shelf held twenty runbooks at its cap. Three were distilled from
runs that were never alerts: a shell-command wiring test ("run uname -a and
date -u, then …"), a chat message about decommissioning a test environment, and
a generic "sre-agent" prompt. Each was loaded as instruction into every later
investigation, for weeks, while the highest-volume real condition of the week
could not get a runbook because the shelf was full.

The re-fire gate was exercised by hand right after its deploy — a real prompt
replayed under `manual:refire-check:…` — and the weekly page counted it as a
re-fire answered from a runbook, inflating the one number the change exists to
move. A drill on 2026-09-08 (`drill:resume:*`) left two rows on the work board
that read as work somebody opened and closed.

WebhookWise has `SYNTHETIC_SOURCES` for the same problem: a credential-rotation
probe and a canary whose events are stored, judged and forwarded like any other
but never create an incident or enter the quality and noise analytics. The
principle is the same here — real machinery on unreal work — and the marker
lands on the run rather than on a source because this node's runs arrive through
one door and are told apart by their session key.

**Why a prefix on the key.** Every by-hand exercise and every drill in this
repository's history named its session `manual:…` or `drill:…` already; the
convention existed, nothing read it. Patrols are not synthetic — they are the
loop's own real work and have their own handling — so `patrol:` is not in the
default.

**Why mark at creation rather than test at each reader.** Five readers, one
predicate: a prefix test repeated in five modules is five places to forget the
sixth prefix. The mark is set once in `RunService.start`, and readers ask the
run.

## Consequences

- A synthetic run still runs, still returns its report, still appears on the
  sessions page — it is a real run of the machinery, and that is what it is for.
  What changes is what it is allowed to teach and to count.
- The three test runbooks pruned by hand on 2026-09-14 would not have been
  written under this rule; the manual re-fire check would not have inflated the
  weekly count. Neither is retroactive: the pruned shelf and this week's page are
  as they are.
- A drill that deliberately wants to exercise distillation or the re-fire gate
  has to use a non-synthetic key and clean up after itself, as before. The
  default is that a rehearsal leaves no trace in the knowledge, not that it
  cannot.
