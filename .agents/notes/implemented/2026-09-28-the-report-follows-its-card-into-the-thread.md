---
title: The report follows its card into the thread — the phone is the console
status: implemented
date: 2026-09-28
scope: stack
---

## Decision

The investigator sends its whole report as `analysis.detail` beside the
summary; the pipe carries it on the card model as `detail`, unread; a bridge
that delivers as an application posts it under the card, in the card's own
thread, in pieces of a card's worth each (2,500 characters, at most eight, the
last piece saying "the rest is on the console" when there was more). The card
itself stays a card. A webhook delivery has no threads and leaves the detail
out with a log line, the way it already leaves out the thread hints.

Ported from the successor project's chat adapter, which posts the report's full
text into the topic so a phone reads it without a console
([[pilot-zero-read-back-and-the-order-of-the-next-ninety-days]], port list).

## Why

Pilot zero's cards were read on a phone. The investigator's public URL was
empty on that deployment, 3 of 741 cards carried any link, and the card's
summary is a clip of the first 2,400 characters; the buttons on the card asked
the person to rule on evidence they could not open. The one place the evidence
could have been read is the thread under the card, which the bridge could
already reply into (follow-ups have answered there since 2026-09-08). Nothing
posted the report there.

## Consequences

Every report costs a few more messages in the chat — one per 2,500 characters
— under its card rather than beside it, so the channel's top level does not
grow. A deployment that wants the summary only sets no `detail`; the pipe and
the bridge treat an absent block as absent. The dry-run answer counts the
pieces (`detail_chunks`) so a conformance run can see them without an account.

The work stack is the deployment this changes for its operator; it needs its
three images rebuilt and recreated to pick it up.

## Rejected

- **Putting the full report into the card.** Feishu caps a card and the phone
  caps attention; a card that scrolls for a minute is a card nobody reads to
  the button.
- **A link to the console instead.** Set now where it can be
  (`HOOKPROBE_PUBLIC_URL`), and still not reachable from a phone on a laptop
  deployment; the text has to travel.
