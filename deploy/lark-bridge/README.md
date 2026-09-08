# lark-bridge — the chat's way in and out, kept out of the pipe

One small process, two directions, no state of its own.

**Out.** hookrelay's `feishu` channel renders a card and POSTs it the way a
custom-bot webhook would. The bridge accepts that exact body and sends it through
the Lark message API **as the application** — which is the whole reason it
exists: a custom bot can only send, so the buttons on its cards have nowhere to
call back to, and it cannot reply inside a thread. When the pipe adds `reply_to`,
the card goes out as a reply in that thread; when it adds `chat_id`, it goes to
that chat, so one bridge serves several channels.

**In.** The bridge dials out to Lark over a long connection and consumes two
event streams: `card.action.trigger` (a button press → the pipe's `/card-action`
door, carrying the token the pipe minted) and `im.message.receive_v1` (a person's
message → the pipe's `lark-thread` door). Nothing inbound needs a public route;
hookrelay's public front door stays closed.

Why a sidecar and not a pipe plugin: the pipe is content-blind and caps its own
size ([scripts/assert_weight.py](../../scripts/assert_weight.py)). An IM
platform's auth, token refresh and websocket dialect are none of the pipe's four
jobs — receive, route, deliver, account — and a `feishu` channel pointed at
`http://lark-bridge:9100/` is all the coupling needed to keep them out.

## What a message becomes

| the person does | the bridge forwards | the pipe does |
|---|---|---|
| replies under a card the pipe sent (@-mention required, see below) | `topic: reply`, root = that card's message id | `thread_lookup` finds the card's delivery, its chain and the investigation session → the investigator **continues that session** and answers in the thread |
| posts a top-level message that @-mentions the bot | `topic: new`, root = the message itself | `thread_lookup` finds no card; with `on_new_topic` configured the pipe shapes it (`kind: brief` — a question for the investigator; `kind: task` — a work item for the planner) and the first report lands **inside the topic** |
| replies inside a topic a person opened | `topic: reply`, root = the person's message | the lookup finds the chain through the report that carried that `thread_root` |
| posts a top-level message mentioning nobody | nothing | — |
| anything sent by a bot (the pipe's own replies included) | nothing | — |

The bridge decides only by **structure** — reply or not, mention or not, which
chat, which sender type. It never reads the text; it carries it. The text is
read by the investigator, under the same read-only posture and guards as an
alert, and only for senders in `HOOKPROBE_FOLLOW_UP_SENDERS`
([hookprobe/docs/configuration.md](../../hookprobe/docs/configuration.md)).

## What it refuses, and what it does not keep

- **Chats.** It posts into, and forwards from, exactly `LARK_CHAT_ID` plus
  `BRIDGE_CHAT_IDS`. A card for any other chat is refused with a 400; a message
  from any other chat is dropped. The blast radius is written here, not decided
  by callers.
- **Cards.** With `BRIDGE_INBOUND_SECRET` set, a card must carry the pipe's
  Feishu-style signature (`{timestamp, sign}`, `sign = base64(HMAC-SHA256(key="{ts}\n{secret}", msg=""))`)
  — the same value as the channel's `secret`. Unsigned or wrong → 401, which
  the pipe records as a failed delivery and retries or dead-letters in the open.
- **State.** None. Which card belongs to which alert, which thread to which
  session — that is the pipe's ledger (`platform_message_id` on every sent
  delivery). A bridge can be restarted or replaced without losing a thread.
- **Loops.** Messages whose sender is a bot are ignored, so the pipe's own
  in-thread replies can never re-enter the door.

## The Lark app it needs

**One app per bridge.** Lark allows one event long-connection per app, and
lark-cli refuses to open a second ("another event bus is already connected to
this app"). Two bridges — or a bridge and any other consumer — on one app means
one of them never receives anything. The alert deployment and the work
deployment each run their own bridge on their own app.

Permissions (tenant scopes), in the console's import format:

```json
{"scopes": {"tenant": [
  "im:message:send_as_bot",
  "im:message.group_at_msg:readonly",
  "im:message.group_at_msg.include_bot:readonly",
  "im:message.p2p_msg:readonly",
  "im:message:readonly",
  "im:message:update",
  "cardkit:card:read",
  "cardkit:card:write",
  "im:chat.members:bot_access",
  "im:chat:read"
], "user": []}}
```

Sending and thread replies need `send_as_bot`; receiving the group messages
that mention the bot needs the two `group_at_msg` scopes (`p2p_msg:readonly` is
what the CLI lists as the event's requirement); repainting a pressed card needs
the `cardkit` pair and `message:update`; `bot_access` lets the bot be added to a
group; `im:chat:read` only lets `lark-cli im +chat-list` say which groups it is
in. With these scopes a reply reaches the bridge **only when it @-mentions the
bot**. `im:message.group_msg:readonly` (every group message) removes that
requirement and is a sensitive scope; an @-mention is also a person saying "I
mean to spend this turn", so the default is to leave it off.

Console settings that are not scopes: **Bot** capability on; **Events &
Callbacks** → subscription mode *long connection* (no request URL); under
*Event Configuration* add `im.message.receive_v1`; under *Callback
Configuration* add `card.action.trigger` — it is a callback, not an event, and
that tab is where people fail to find it. Publish a version. Then a person adds
the bot to each chat the bridge serves: no bot can add itself, and an app
without `im:chat.members:write_only` cannot add another.

## Environment

| variable | default | meaning |
|---|---|---|
| `LARK_APP_ID`, `LARK_APP_SECRET` | *(required)* | the app; written into lark-cli's config on the `/config` volume on first start — see *Switching apps* |
| `LARK_BRAND` | `lark` | `lark` or `feishu` |
| `LARK_CHAT_ID` | *(required)* | the default chat: cards without `chat_id` go here, and replies from here are forwarded |
| `BRIDGE_CHAT_IDS` | *(empty)* | comma-separated further chats this bridge may post into and forward from (`options.chat_id` on a pipe channel names one) |
| `BRIDGE_INBOUND_SECRET` | *(empty = accept every card)* | the pipe's channel secret; set it — the port sits on a network shared with other stacks |
| `RELAY_ACTION_URL` | `http://hookrelay:8100/card-action` | where a button press goes |
| `RELAY_THREAD_URL` | *(empty = do not listen for messages)* | the pipe's `lark-thread` door, e.g. `http://hookrelay:8100/hook/lark-thread` |
| `THREAD_SECRET` | *(empty)* | signs message forwards for that door (`X-Hook-Timestamp`, `X-Hook-Signature` = hex HMAC-SHA256 over `"{ts}.{body}"`) — the door's `${LARK_THREAD_SECRET}` |
| `BRIDGE_PORT` | `9100` | where the pipe posts cards |

The image pins `@larksuite/cli` (`ARG LARK_CLI_VERSION`); lark-cli keeps its
credentials and event cursor under `/config`, a named volume in both composes
(`deploy/docker-compose.shadow.yml`, and the `bridge` profile of
`deploy/docker-compose.work.yml`).

## Wire shapes

Inbound card (what the pipe posts):

```json
{"msg_type": "interactive", "card": {"...": "..."}, "timestamp": "1700000000", "sign": "...",
 "reply_to": "om_… (optional: post as a reply in this thread)",
 "chat_id": "oc_… (optional: this chat, if served)"}
```

Answer: `{"ok": true, "message_id": "om_…"}` — the id the pipe writes onto the
delivery, and the handle a reply under that card will quote.

Outbound message (what the bridge posts to `RELAY_THREAD_URL`):

```json
{"root_message_id": "om_…", "topic": "reply | new", "message_id": "om_…",
 "sender": "ou_…", "chat_id": "oc_…", "text": "the message, mentions stripped"}
```

Outbound press (to `RELAY_ACTION_URL`): `{"action": {"value": {"hookrelay_action": "<token>"}}, "actor": "ou_…"}`
— the token is the authorisation; the bridge never inspects it.

## Operating it

- **Is it connected?** `docker exec <bridge> lark-cli event status` → `Bus:
  running`, `Active consumers: 2` (presses and messages). The log shows
  `listening for events (key=…)` for each, and `remote connection check:
  online_instance_cnt=0` on connect.
- **"another event bus is already connected to this app (1 remote event
  connection)"** — something else holds this app's one connection: another
  bridge, a laptop's `lark-cli event consume`, another deployment. Not a
  version problem, not this container. Find and stop the holder, or give this
  bridge its own app. The bridge keeps retrying (60 s backoff) and takes the
  slot when it frees.
- **Switching apps.** The entrypoint leaves an existing lark-cli config alone,
  so changing `LARK_APP_ID` in `.env` is not enough: run
  `docker exec <bridge> lark-cli config remove` (or drop the volume), then
  recreate the container. Add the new bot to every served chat first, or cards
  fail with `Bot/User can NOT be out of the chat` until it is.
- **Reading the logs.** `card delivered message_id=om_…` (and `(in thread)`
  for replies); `thread reply forwarded: 200 {…}` with the pipe's decision;
  `card refused: chat … is not one this bridge serves`; `press forwarded`.
  The pipe's side of every message is an event of source `lark-thread` in its
  ledger — `unknown_thread` when the root was not a card of ours and no topic
  shape is configured, `resolved` or `new_topic` otherwise.
- **`--dry-run` says `unknown`.** `lark-cli event consume <key> --dry-run`
  can only confirm `console_event_published` when the app has
  `application:application:self_manage`; without it the answer is `unknown`,
  not a failure. The connect itself is the test.

See [docs/deployments.md](../../docs/deployments.md) for how the two
deployments use it and [docs/containment.md](../../docs/containment.md) for what
the chat sender allowlist does and does not stop.
