---
title: The pipe's board reads like an inbox, in the reader's language
status: implemented
date: 2026-09-29
scope: hookrelay
---

## Decision

The pipe's board was rebuilt a second time, from the page's structure up,
around how people already read an inbox. The seven stages of
`2026-09-28-the-board-is-drawn-as-the-seven-stages-of-one-alert` stay exactly as
they were — the same names, the same order, colour earned by state, every
judgement read off the pipe's own identifiers. What changed is everything
around them.

- **One sentence first.** The top of the page says whether anything is waiting
  on you — "6 things are waiting on you", or "All clear" — with a button that
  filters to them, beside a day of alerts stacked by where each one stands.
  Four numbers under it (waiting on you, in flight, delivery failures, ended
  well in the last day) each open the list already filtered.
- **One row per alert.** A status in a word, the title, the one line that
  matters now — what the card asked and when, who pressed and how long after,
  why a delivery failed — the source, the time and the seven dots. Rows are
  grouped by day; quiet events are one checkbox away; filters by status and
  source; a tag when the judge grouped a burst into one incident.
- **One click for the whole story.** A row opens a drawer from the right: the
  seven stages top to bottom, then every delivery with a retry beside a dead
  one, the bytes of both directions message by message, and the audit record
  in time order. The drawer has an address (`#/alert/<handle>`), Back closes
  it, and the links already out there — `#journey=`, `#chain=`, `#timeline`,
  `#events`, `#config`, `#help` — still land somewhere sensible.
- **Each action where it is looked for.** The tabs are alerts, ledger,
  deliveries (per-channel counts, dead letters with retry, breakers, cards
  nobody opened, the fuse), silences (the list with lift, a form with presets;
  silencing every source asks first), routing (hazards; doors, gates and exits
  with unreachable routes greyed; the config editor with an unsaved marker and
  a leave-anyway guard; a dry run) and help. A write that meets a 403 asks for
  the admin token once and tries again.
- **Chinese or English.** Every string is in one JSON block in the page, a set
  per language, chosen by the browser and remembered once switched.
  `hookrelay/tests/test_board.py` holds the two sets to the same keys and the
  same placeholders, and every key the script asks for to the set.
- Still one file, no timers, and the five pinned blocks byte-identical to the
  other two boards (`scripts/assert_design.py`).

Two facts under the page changed with it, and the morning card with them:

- **Only a card that arrived asked anybody.** A delivery's `asked` on
  `/status` (and so a hop's on `/timeline`) now carries button labels only
  when it was sent. The board's precedence moved with it: an alert that ended,
  or whose card is still waiting on a press, reads as that even when one of
  its deliveries gave up; a failed delivery is the row's status when nothing
  outranks it, and has its own banner, tile and tab regardless.
- **An unwritable config is a 409.** `PUT /config` answers 409 with the reason
  and what to do instead — edit it where it is mounted from, then reload —
  removes its temp file, and leaves the running config as it was.
- `scripts/needs_you.py` counts an approved procedure nothing has answered as
  in flight, and dates an ending from when it ended, so an alert that fired
  yesterday and recovered this morning ended well today. Both are rules the
  board drew and the card did not.

## Why

The operator's words: 「你先把 relay 页面好好设计一番，推倒重来，功能完整，页面重新设计。符合人类使用习惯」.
The 09-28 board had the right shape for one alert and the wrong shape for a
page: a wall of equal cards, a separate journey tab to see one alert's story, a
config tab holding everything else, and English only. Nothing on it answered
"is anything waiting on me" in the first second, and the actions a person looks
for — retry this, silence that — were not where the thing that needed them was
shown.

The layout copies what readers already know rather than inventing one: a mail
client's inbox (a headline count, rows by day, one line of preview, a pane
that opens in place and closes with Back), a parcel tracker's vertical
timeline for one item, and an app's settings drawer. None of it needs
explaining, which is the point.

Checking it on a scratch stack with the sink stopped found the two facts. Two
alerts whose every card had died were listed as **waiting on you**, with
buttons "sent to ops-feishu": every attempt keeps the body it tried, for the
dispute, and the labels were read off that body whatever became of the
delivery. The morning card read the same labels and would have said the same.
Nothing that only loaded the page could see it, because the board and the card
were wrong the same way. And Save in the config editor answered a bare 500 on
every compose this family ships, because all of them mount the config
read-only; the page called that "validation failed" and sent the reader
looking for a typo.

## Consequences

- `asked` means "a person was asked", not "the pipe tried to ask". What a
  failed card would have asked is still in its bytes on `/trace`.
- A row takes the most useful fact as its status, so a failed delivery can sit
  under "waiting on you" or "resolved". Every failure is still counted on the
  tile, named in a banner and retryable from the deliveries tab and from the
  alert's story; that tab, not the list, is where to count failures.
- The strings are data in the page. A new sentence is a key in both sets or the
  test is red; at runtime a missing key falls back to English and then to the
  key itself, which is why the test exists.
- The docs' pictures of the board were reshot from that scratch run with every
  state on screen at once — in flight, a failed delivery, cards waiting, a
  ruling, a recovery, a fix that held. They go stale with the next layout
  change, like every picture.
- The judge's and the investigator's pages are unchanged; they share the pinned
  blocks and nothing else. The pipe weighs 5,931 against its 6,000 ceiling.
