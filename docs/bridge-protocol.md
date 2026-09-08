# The chat-bridge protocol

How hookstack talks to a chat platform without knowing which one. The pipe
sends a **card model** — five blocks as plain facts — to a small sidecar, the
*bridge*, that renders it in its platform's dialect, posts it as the platform's
application, and hands back what the platform called the message. Presses and
replies come back through the bridge to two of the pipe's doors. Everything
platform-specific — the card schema, the markdown dialect, auth, long
connections, "which scopes does the app need" — lives in the bridge.

[deploy/lark-bridge](../deploy/lark-bridge/README.md) is the first bridge. A
bridge for another platform implements the three shapes below and passes the
same tests; nothing in the pipe, the judge or the investigator changes.

This is `hookstack-bridge/1`. The version rides in every outbound body, and a
bridge refuses a version it does not speak rather than guessing.

## 1. Outbound: a card

The pipe's `bridge` channel type `POST`s one JSON object per message to the
bridge's URL:

```json
{
  "protocol": "hookstack-bridge/1",
  "card": {
    "title":   "📡 Single top-up over 500",
    "tone":    "high",
    "lead":    "🔴 HIGH",
    "summary": "three large top-ups in nine minutes across two accounts",
    "crumb":   "demo-alarm · prod · Single top-up over 500",
    "impact":  "limited to the notification itself",
    "links":   [{"text": "Large top-up runbook", "url": "https://kb.example/runbook/42"}],
    "actions": [{"text": "Acknowledge", "style": "primary", "value": {"hookrelay_action": "…"}}],
    "footer":  "grafana · business · 2026-08-07 10:32:50"
  },
  "reply_to": "om_… (optional: post as a reply in this message's thread)",
  "chat_id":  "oc_… (optional: post to this chat, if the bridge serves it)",
  "action_link_base": "https://… (optional: where an action LINK should land, for a delivery that cannot call back)"
}
```

Every key under `card` is optional except `title`. A plain event (a channel
without `payload: processed`) arrives as `title`, `tone`, `summary`, `details`
(one `name: value` per line) and `footer`. `tone` is one of `critical`, `high`,
`medium`, `low`, `info`, `recovery` — the **state**, not a colour; the colour it
earns is the bridge's decision. All text is **unescaped plain text**: the bridge
is the one place that knows which of its slots render markup, so it escapes
there (a `<at id=all>` in an alert title must not page a company). `actions`
carry values the pipe already signed; a bridge puts them into buttons and
returns them on a press without reading them.

Headers: `content-type: application/json`; when the channel has a `secret`,
`X-Hook-Timestamp` (epoch seconds) and `X-Hook-Signature` = hex
HMAC-SHA256(secret, `"{timestamp}.{body}"`) over the **exact bytes** posted —
the pipe's own scheme, the same one the bridge uses for the messages it sends
back. A bridge refuses a missing or wrong signature and anything older than
five minutes with `401`. The pipe also sets `X-Hook-Idempotency-Key` and
`X-Hook-Correlation-Id`; a bridge may ignore them.

Response: `200 {"ok": true, "message_id": "<the platform's id>"}`. The pipe
writes that id onto the delivery, and it is the handle a reply under the card
later quotes. `400` for a body it cannot use (no card object, a chat it does not
serve), `502` when the platform refused — the pipe retries and dead-letters in
the open, which is the visible failure everyone wants.

**Dry run.** With the header `X-Hookstack-Dry-Run: 1` the bridge validates,
authenticates and renders but does not send, answering
`{"ok": true, "dry_run": true, "message_id": "", "rendered": <platform payload>}`.
This is the conformance hook: a deployment can prove the whole path except the
last hop from inside its own network, and a new bridge can be tested without
an account on its platform.

**Webhook delivery.** A bridge may deliver through a platform's *incoming
webhook* (a custom bot) instead of as an application. The protocol is the same;
what changes is what the platform allows: no message id comes back
(`"message_id": ""`), `reply_to` and `chat_id` are ignored with a log line (a
webhook has one destination and no threads), and `actions` are rendered as
**links** to `{action_link_base}/card-action?t=<token>` — the pipe's confirm
page — because a custom bot's buttons cannot call back. No base, no links; a
button that does nothing is never drawn. The pipe puts `action_link_base` in
the envelope when the channel's `options.action_link_base` says so (its own
`HOOKRELAY_PUBLIC_URL`, as a browser reaches it). lark-bridge switches to this
mode with `LARK_WEBHOOK_URL`; the quickstart runs it that way against its sink.

## 2. Inbound: a message

When a person writes where the bridge listens, the bridge `POST`s to the pipe's
message door (the deployment names it; ours is `lark-thread`):

```json
{
  "root_message_id": "om_…",
  "topic": "reply | new",
  "message_id": "om_…",
  "sender": "ou_…",
  "chat_id": "oc_…",
  "text": "the message, with the mention of the bot stripped"
}
```

`topic: reply` — the message is under something; `root_message_id` is the
message it replies to (a card the pipe sent, or a person's own topic).
`topic: new` — a top-level message that addressed the bot; `root_message_id` is
the message itself. Signed with the door's secret in the same
`X-Hook-Timestamp` / `X-Hook-Signature` headers. The bridge decides only by
**structure** — reply or not, mention or not, which chat, human or bot — and
never reads the text; the pipe's `thread_lookup` stage resolves the root to a
chain and an investigation session, and the investigator reads the text under
its own posture and its sender allowlist.

## 3. Inbound: a press

A pressed button reaches the pipe's `/card-action` door as:

```json
{"hookrelay_action": "<the token from the button, unchanged>", "actor": "ou_…"}
```

The token **is** the authorisation — minted, signed and single-use by the pipe
— so the door needs no other credential from the bridge; `HOOKRELAY_CARD_CALLBACK_SECRET`
adds the header signature on top for deployments that want defence in depth.
The bridge then repaints the pressed card to say what happened; how is its
business.

## What a bridge must not do

- Keep state about cards, threads or sessions. Which card belongs to which
  alert is the pipe's ledger; a bridge restarts without losing a thread.
- Read message text or action values. It carries them.
- Post into, or forward from, chats it was not told to serve. The blast radius
  is the bridge's configuration, not its callers'.
- Forward anything a bot wrote, its own replies included. That is the loop guard.

## Conformance

- The pipe's side: `hookrelay/tests/test_bridge_channel.py` asserts the bridge
  channel type produces exactly
  [`deploy/lark-bridge/contract/outbound-card.json`](../deploy/lark-bridge/contract/outbound-card.json).
- The bridge's side: `deploy/lark-bridge/tests/test_protocol.py` drives a
  bridge with that same fixture — accepted, rendered, sent where it asks;
  tampered, stale or unsigned refused; dry run renders and sends nothing;
  an unserved chat refused. A new bridge copies that file and points it at
  itself. The other two shapes have examples beside the fixture
  (`inbound-message.json`, `inbound-action.json`).
- On a deployment: a signed dry run from the pipe's container to the bridge
  proves the wiring, the secret and the rendering without posting anything.

## Why the seam is here

The pipe is content-blind and keeps a weight ceiling. Its four jobs — receive,
route, deliver, account — do not include knowing a platform's card schema, and
they cannot include a platform's websocket. Before this protocol the pipe had
a `feishu` channel type that rendered Feishu JSON and posted it to the bridge,
which only forwarded; adding Slack would have meant a Slack channel type in the
pipe **and** a Slack bridge. Now it means a Slack bridge. The pipe's outbound
kinds are exactly two: `generic` (signed JSON, for machines) and `bridge` (a
card model, for people). Custom-bot webhooks are the bridge in webhook mode
for Feishu, and a shipped plugin (`examples/plugins/chat_markdown_channels.py`)
for the DingTalk and WeCom markdown dialects — an in-process bridge, rendering
the same model.
