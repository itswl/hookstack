---
title: A door may feed no brain, and the guard now says which invariant it meant
status: implemented
date: 2026-09-03
scope: stack
---

## Decision

`shadow.yaml` gains a `watch` source and a terminal `watch-to-me` route
(`priority: 100`, `stop: true`) that delivers straight to the operator and
never reaches a judge. It is signed, unlike the loopback doors, because it
arrives from outside the compose.

`assert_shadow_config.py` stops requiring that every source reach every brain
and asserts the two things that sentence was standing in for:

1. **no partial fan-out** — a source feeding one brain feeds all of them;
2. **no starved brain** — every brain is fed by at least one source.

A source that feeds zero brains is now legal.

## Why

The watcher's signals arrive already judged, by a prompt tuned for exactly this
question ("does Adrian need to act"). The judges are calibrated on alerts:
severity keywords in Chinese alert vocabulary, recovery semantics, flap
suppression, an eval set of alert scenarios. Handing them a colleague's
question would spend three model calls to re-judge it in a vocabulary that
means nothing here, and file the answer in a ledger whose comparisons are about
alert severity.

The old check would have forbidden that bypass. It should not have: its own
comment already said what it was protecting — "what must not happen is a brain
nobody routes to" — and that is a statement about brains, not about sources.
This is the second narrowing for the same reason; the first was when a return
door and a card channel appeared and "every source reaches every channel"
became wrong. Writing both invariants out separately is what stops a third
round.

## Consequences

A source can now be added that quietly reaches nobody, and the guard will not
object as long as the brains are fed elsewhere — the price of allowing a
deliberate bypass. The partial-fan-out check is what still catches the case
that actually corrupts data: a source reaching two brains out of three, whose
missing third reads as agreement.

Signals on this route get the pipe's delivery, retry, dead letters and ledger,
and none of the judging — which also means none of the alert stack's
suppression. That is the intent: the watcher is recall-first, and silences,
dedup windows and importance capping were all built to reduce.
