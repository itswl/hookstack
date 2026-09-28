---
title: The morning's numbers arrive as a card
status: implemented
date: 2026-09-28
scope: stack
---

## Decision

`scripts/needs_you.py` computes the board's four figures — cards that asked
and nobody pressed, alerts in flight, dead letters, conditions that ended or
fixes that held in the last day — from the pipe's two feeds, by the rules the
board uses, and prints them as one signal. `scripts/needs_you.sh` posts it
through the pipe's `watch` door as `low`, `kind: report`, from a host crontab
every working morning. On the work deployment the `just-tell-me` route makes
it one card in the watch group and funds nothing. The card's title is the one
line a person decides from: *Needs you · 2 waiting · 09-29*, or *Nothing
waiting on you*. The board folds `kind: report` events into its quiet line, so
the card does not appear on the board as an alert about itself.

## Why

The operator reads on a phone. The board's numbers live on a page the phone
cannot open, which is the exact shape the 2026-09-08 parking note named: an
overview nobody opens has no value however cheaply it is built, and the useful
form of one is a signal — the one thing that needs you finds you. The weekly
page already travels this road; the morning card is the same road with a
shorter question.

Computed from the feeds rather than by a new pipe endpoint, for the reason the
weekly page is: the pipe stays four jobs, and a script standing where the
operator's tokens already are can read what it needs.

## Consequences

- The rules are duplicated, deliberately, in two places: the board's script
  and `needs_you.py`. They read the same fields (`asked`, `is_recovery`, the
  verdict's title, `card-action` hops) and the test pins the figures on one
  fixture; if the board learns a new rule the card should learn it the same
  day, and the note that changes one should name the other.
- A card every morning, including *Nothing waiting on you*. That is also the
  heartbeat: the clock, the pipe and the chat were up.
- The clock is the host's cron; a laptop asleep at 09:35 sends nothing that
  day and says nothing about it. The absence sweep does not watch this door for
  this signal. Acceptable for one operator; a second deployment would move the
  clock into a container, as the watcher's timer is.
