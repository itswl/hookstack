---
title: The Lark adapter is a sidecar, and a button press needs no public door
status: implemented
date: 2026-08-20
scope: stack
---

## Decision

A card that can be acted on is sent by the Lark **application**, not by a custom
bot webhook, and the application half lives in a sidecar container
(`deploy/lark-bridge/`) rather than in hookrelay. Two directions, one process:

    out  the pipe's existing `feishu` channel renders a card and POSTs it, in
         the custom-bot shape, to the bridge — which re-sends it through the
         Lark message API as the application.
    in   the app's `card.action.trigger` events arrive over a LONG CONNECTION
         that the bridge dials out on; the press is forwarded to the pipe's
         `POST /card-action` on the container network.

hookrelay is unchanged. Its `feishu` builder already produces exactly the shape
the bridge accepts, so the entire coupling is a channel URL pointing at
`http://lark-bridge:9100/`.

## Why

**A custom bot cannot receive.** The `bot/v2/hook/...` webhook only sends, so
buttons on its cards have nowhere to call back to. Every feedback number this
family grew — `mattered_pct`, the investigation ruling, the escalate-if-untouched
sweep — rests on a press being receivable, so half the work was unusable until
the sending identity changed.

**And it needs no inbound route, which was the blocker.** An earlier reading of
this concluded that app callbacks would still require a public URL and that
hookrelay's public front door — deliberately rolled back on 2026-08-07, recorded
in the Caddyfile — would have to be reopened. That was wrong. Lark's event
subscription supports a long connection: the consumer dials out. Verified end to
end before any of this was written: a real button press arrived over the
websocket with the signed token intact in `action_value`, and the pipe's own
verifier accepted it.

**Why a sidecar and not a hookrelay plugin.** The pipe is content-blind and now
carries a ceiling on its own size that a check enforces. An IM platform's auth,
token refresh and websocket dialect are none of its four pillars. Putting them
behind a channel URL keeps the pipe's ledger the thing that reports whether a
card landed — the bridge answers 502 on a Lark rejection precisely so the outbox
retries and dead-letters in the open rather than swallowing an alert.

## Consequences

- The shadow deployment stops being silent. Brain A now returns
  (`SHADOW_RETURN_URL`), brain B stays ledger-only — two brains both returning
  would put two cards in the chat for one alert, and B's opinion is read from
  the ledger where the agreement comparison already lives.
- The verdict's route is terminal (`stop: true`, priority 100). Without it a
  returned verdict falls through to the fan-out and is judged again — the pipe
  feeding a brain its own output.
- `silence` is deliberately NOT offered on these cards. A press would quiet the
  shadow's copy, not the platform's notification: muting the observer, one click
  away, while the thing being observed keeps firing.
- The bridge holds its child's **stdin open**. lark-cli treats stdin closing as
  "stop gracefully", so inheriting a container's closed stdin made it connect,
  report ready and exit in the same millisecond, then reconnect forever — which
  looked exactly like nobody pressing anything.
- Credentials come from the environment and are installed with lark-cli's own
  `config init --app-secret-stdin`. A first attempt hand-wrote the CLI's config
  file and the CLI ignored it: the on-disk shape is its internal business, not a
  contract. The secret goes in on stdin, never argv, because a process list is
  readable.
- `scripts/stack-smoke.sh` exports the bridge's three required variables, so the
  new service is covered by the same parse that covers everything else.

## Rejected

- **Sending through the custom bot webhook.** Simpler, already configured, and
  produces cards whose buttons do nothing — worse than no buttons.
- **Reopening hookrelay's public front door for the callback.** Unnecessary once
  the long connection was understood, and it would have undone a deliberate
  rollback for a reason that did not exist.
- **A `lark_app` channel type inside hookrelay.** It would put OAuth token
  refresh and a websocket client inside the content-blind pipe, against both the
  doctrine and the size ceiling.
