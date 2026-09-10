---
title: Two kinds of waiting — a card nobody decided on, and a card nobody saw
status: implemented
date: 2026-09-10
scope: stack
---

## Decision

A fourth, optional shape in `hookstack-bridge/1`: the pipe asks a bridge **has
anybody opened these**, by message id, and the bridge answers with a reader
count and a first-read timestamp. `GET /unseen` on the pipe turns that into the
question a person actually has: *which cards did I send that nobody has opened?*

Three answers per card, and the third is load-bearing: **seen**, **unseen**, and
**unknown** — a bridge that could not be asked, a channel that is not a bridge,
or a platform that will not report on that message.

Counts and timestamps, never who. Asked on demand, never polled, nothing stored.

## Why

A board that says `waiting_approval` cannot say WHICH waiting it is. Production
carried three remediation proposals for days and there was no way to tell "a
decision nobody has made" from "a card nobody has seen" — different failures,
different fixes, same row. The handle to tell them apart already existed:
`platform_message_id` has been on every delivery since the thread work, and
nothing had ever asked the platform about it.

Proven reachable before it was designed, which is why it got built at all: one
`GET /open-apis/im/v1/messages/{id}/read_users` against the live app returned a
reader and a timestamp **3 seconds after that card was sent**. The scope it
needs (`im:message:readonly`) was already granted.

**Why the pipe and not a brain.** Read state is delivery state — the same axis
as `queued -> sent -> dead` — it reads no alert content, judges nothing, and the
pipe is the only component that holds a platform message id. It passes
hookrelay's own doctrine test ("a property of a good PIPE, or a judgment about
the alert's worth?") on every clause.

**Why on demand, and nothing stored.** The question is only asked while somebody
is looking. A poller would query the platform about every card forever, and a
stored answer goes stale in exactly the direction that matters — unread becomes
read, never the reverse.

**Why never who.** The count answers the question; the identities are the part a
pipe's ledger has no business accumulating as a side effect. A deployment that
wants names can ask the platform deliberately.

**Why `unknown` is its own answer.** An un-askable card reported as unread would
be this feature telling the exact lie it exists to stop. That is not
hypothetical: the first live run returned **59 unknown, 0 seen, 0 unseen** —
correct, and useless, and it is how the two real bugs were found (the pipe asked
about every candidate at once, and reused the 10s delivery timeout for a
question the far end answers with one API call per id). Bounded to 20 with its
own 45s timeout, the same run reads **20 checked, 20 seen** in 20.1s.

## Consequences

* hookrelay's ceiling 5500 -> 5650, with the reason in `CEILINGS` and the same
  number in its README. The per-module question the 2026-09-04 entry left open
  is answered separately in `2026-09-10-the-ceiling-stays-per-service.md`.
* About a second per card, so this is a question you ask about a screenful, not
  a report you run over a month. The default window is 24h and the default limit
  is 20; both are parameters and `checked` says what was actually asked.
* A bridge that does not implement shape 4 answers
  `{"ok": true, "supported": false, "read": {}}` and every card reads `unknown`.
  lark-bridge does that in webhook mode, where a custom bot never had an id.
* **An unseen card says what it was asking for.** `card.actions`, read from the
  stored `sent_body` — the exact octets that left the socket, kept for this kind
  of question — and counted as `unseen_asking`. An unseen notification is noise;
  an unseen card with a button on it is somebody waiting on an answer nobody was
  ever asked for. Reading that block is not reading the alert: the pipe wrote
  it, and the labels are the pipe's own text.

  It cannot be proven live on the work deployment: `work.yaml` carries no
  `card_actions`, so no card it sends has a button. (Its comment for that — "no
  bridge, so no callback can reach this pipe" — is now stale, since that
  deployment grew two `bridge` channels. Left alone: turning card actions on
  there is the operator's decision, not a tidy-up.) The shadow config mints
  them, so this reports for real where the waiting proposals actually are.
* **Not yet on any board.** This is a pipe route; hookprobe's work board still
  says `waiting_approval` without saying whether the card was seen, because the
  probe never learns the platform message id. Joining them is the obvious next
  step and it is a plumbing change on the return path, not a new idea.
* Live verification covered `seen` and `unknown`. `unseen` is covered by tests
  only — manufacturing a real unread card means posting one nobody opens.
