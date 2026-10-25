---
title: The condition and the doctor — quieting the alert in front of you, and naming the locks before a person presses
status: implemented
date: 2026-10-26
scope: stack
---

## Decision

**A silence now names a condition, and a card names its own alert.**

The `silences` row gained `condition TEXT NOT NULL DEFAULT ''` (empty keeps the
old source-wide meaning). The silence stage asks with the extracted title, and
the narrowest live row wins over a source-wide one. A card press resolves the
alert behind the card (`Store.event_origin` walks the same quoted-event chain
`/trace` walks) and writes the exact title; a press on a verdict or a report
therefore quiets the alert, not the `judge-notify` return door. `scope: source`
is the deliberate widening, and the board's silence form can name a condition or
leave it empty for the whole source.

**`scripts/doctor.py` names the first-run locks.**

A host-side, stdlib-only check of `.env`, the deployed pipe config and
(optionally) the running services. It reports must-fix vs should-fix, with one
fix per line: read/admin/probe tokens, the action secret, model credential,
console link, remediation allowlist (empty file included), `approve` and the
ruling pairs in `card_actions`, off-host backup, and each service's health /
posture when an address is given. Exit 1 only when a must-fix is present.

## Why

Pilot zero's three locks were configuration, not code: no `approve` in
`card_actions`, an unreachable console link, and an empty allowlist. Each was
visible only by reading several files and two containers at once. The doctor is
that reading, done once and repeatable.

The silence default was worse than source-wide in the code: a card action with
no `source` parameter defaulted to `*`, so one "Silence 15m" muted every door.
Resolving the card's origin fixes the scope and, while there, makes the narrow
condition the default. A single operator's most common noise action is "quiet
this alert for an hour", not "quiet everything".

## Consequences

- Existing silence rows (condition `''`) keep their source-wide meaning. New
  card presses are condition-scoped; the board shows the condition column and
  the dialog offers "whole source instead".
- A card whose event id has been purged cannot resolve an origin and refuses
  rather than guessing `*`.
- `POST /silences` accepts `condition`; the old shape is unchanged.
- `python3 scripts/doctor.py` is now the first command in the quickstart's
  real-deployment path. It does not write or start anything; a missing service
  is a warning, a missing credential or button is a must-fix.
