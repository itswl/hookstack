---
title: The watcher's signature is held where no agent runs
status: implemented
date: 2026-09-30
scope: stack
---

## Decision

`WATCH_INGEST_SECRET` leaves the watcher's container. `deploy/watch-signer`
holds it; `probe-watch` posts its signal there unsigned, with a bearer token,
and the signer signs it only after checking it.

- It names a conversation the scanner offered THIS round. The signer reads
  `scan.json` at post time, so the set is the round's own rather than a
  remembered one — which is also why it does not inherit the staleness problem
  `scripts/assert_node_contract.py` documents at length.
- Level and kind come from closed sets; anything else becomes `low` and `note`.
  Title and detail are cut to 200 and 4000 characters.
- A round may post twenty signals. The busiest real round posted four.
- `scripts/post_watch_signal.py` takes this path when
  `HOOKSTACK_WATCH_SIGNER_TOKEN` is set, and signs from `.env` as before when it
  is not. The mode is selected by the presence of a token rather than a flag,
  because a flag is a thing to forget.
- `work-data/probe-watch-secrets/watch.env` is deleted. The secret is in `.env`,
  which is where the compose reads it for the signer.

## Why

Signing from a file is right on the operator's laptop: the secret never leaves
the host it belongs to. Inside `probe-watch` it was the same arrangement wearing
the same clothes and meaning the opposite. The process deciding WHAT to post is
an agent whose input is colleagues' chat messages, and the secret sat in a file
it could read.

So the door's only check — "this was signed by something that holds the secret"
— answered yes to anything an injected round chose to say. A fabricated request
from a colleague, at `high`, as a `task`, buys a paid planner run and a card the
operator reads as real. The same day's finding about the chat MCP
([[the-chats-tools-are-on-a-list-in-another-container]]) was the same shape: a
list the agent was trusted to respect rather than a place it could not reach.

## Consequences

- An injected round can still distort what it read. It cannot manufacture a
  request from a conversation it was never handed, which is the line between a
  distorted signal and an invented one. That limit is stated in
  `docs/containment.md` rather than implied.
- A deployment with no prescan writes no `offered`, and the signer passes those
  through. Refusing them would be this boundary silencing the watcher it exists
  to keep honest.
- The laptop's own callers (`needs_you.sh`, `weekly_page.sh`) are unchanged:
  no token, so they sign from `.env` exactly as before.
- Verified against the live deployment: a signal about an offered conversation
  was signed and landed; one about a conversation nobody offered was refused with
  the offer printed beside it; the watcher's container now holds no copy of the
  secret in any file or variable.
