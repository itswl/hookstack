---
title: The pipe's board is drawn as the seven stages of one alert
status: implemented
date: 2026-09-28
scope: hookrelay
---

## Decision

The pipe's board was rebuilt around one shape. Every operation — an alert and
everything that quoted it — is a card drawn as the same seven stages in the
same order: **received · judged · notified · investigated · a person ·
condition · fix**. A filled dot is a stage that happened, the wait between
two stages sits on the line between them, a hollow dot is a stage that did
not happen, and colour is earned: amber for a card that asked somebody
something and got no press, green for a condition that ended or a fix that
held, red for a fix that did not hold. A pill and one sentence under the
strip say what the alert needs now. Opening a card shows the same seven
stages top to bottom with everything each one said, from `/trace`.

The tabs are **now** (the cards, newest first, with four numbers above them:
waiting on you, in flight, dead letters, ended well in the last day), **journey**
(the stepper for one alert from any handle), **ledger** (every event as
recorded — the forensic view the old events tab was), config and help. Quiet
chains — ticks, repeats, silences, single hops nothing answered — fold into
one line under the cards.

Two feeds grew what the strip needs and nothing heavier: `/status` rows carry
`is_recovery` and, per delivery, `asked` — the button labels the pipe put on
that card, never its body — and `/timeline` hops carry the same `asked`.
`button_labels` moved from app.py into store.py so both feeds and `/unseen`
read one function. Pipe ceiling 5,900 → 6,000 (+11 source, +5 code).

## Why

The operator's words, after opening the board the journey tab had just been
added to: 「当前页面打开没有让人看的兴趣」— nothing on it made anyone want to
read on. It was true. The events tab was the ledger, every row a tick or a
repeat beside the alerts; the timeline drew chains as a line of door names in
small type; the journey was a log. All three were the pipe's books shown as
books. None answered the question a person opens the page with: *what became
of it, and what needs me.*

The seven stages are the answer's shape, and they are fixed on purpose. A
board that draws each chain as its own sequence of doors is accurate and
unreadable — the eye has to parse every card. Seven names in one order, the
same on every card and in every journey, is a shape the eye learns once. A
stage that did not happen stays on the card, hollow, because "nobody was
told" and "nobody pressed" are facts a reader needs to see beside the ones
that did happen.

Everything the strip says is still the pipe's own account. "Waiting on you"
is a card that carried a button (a label the pipe wrote) and no press in its
chain; "resolved" is a later event of the same source and title that its
source stated as a recovery; "fix held" is a return whose title the
investigator wrote. No alert text is read, no node is called, no console is
reached across origins. The board that was refused on 2026-09-08 — the pipe
interpreting the probes' boards — is still refused; what changed is how the
pipe's own rows are drawn.

## Consequences

- The reading view and the forensic view are now different tabs. `now` is
  what an operator opens; `ledger` is what a dispute opens. The events tab's
  code is the ledger tab's code, restyled and otherwise unchanged.
- The strip's judgements are heuristics over identifiers, and the note names
  them so nobody mistakes them for facts the pipe holds: a hop is the judge's
  when its `fields.brain` says so or its door is named `judge…`; a hop is an
  investigation when it carries a session and is not a verdict card; a
  delivery is a card when it asked something or the platform gave it an id;
  "in flight" is a routed origin with nothing back within two hours. A
  deployment whose doors are named differently gets hollow dots where it
  should get filled ones — the sentence under the strip stays right, because
  it is built from the same rows.
- The four numbers are counted over the last 400 events, like the timeline.
  A deployment busier than that in its window under-counts, and the number
  says nothing about it; `/metrics` remains the source for anything that
  goes on a graph.
- `#events` and `#timeline` still open something sensible (the ledger and
  the now tab); `#chain=<hop>` opens the card that hop belongs to;
  `#journey=<handle>` is unchanged.
- Every documented picture of the pipe's board is stale until reshot.
