# lark-bridge — the chat's way in and out, kept out of the pipe

One small process, two directions, no state of its own.

**Out.** hookrelay's `bridge` channel sends a **card model** — the
[chat-bridge protocol](../../docs/bridge-protocol.md): title, tone, summary,
links, actions, as plain facts. The bridge renders it into a Feishu card
(`render.py` — the card schema, its colours and its `lark_md` escaping live
here and nowhere in the pipe) and sends it through the Lark message API **as
the application** — which is the whole reason it exists: a custom bot can only
send, so the buttons on its cards have nowhere to call back to, and it cannot
reply inside a thread. When the pipe adds `reply_to`, the card goes out as a
reply in that thread; when it adds `chat_id`, it goes to that chat, so one
bridge serves several channels. A finished Feishu card from a `feishu`-type
channel is still accepted and passed through, so either type can point here.

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

One topic is one investigator run and one engine session; each reply in it is
another turn of that session, which is why follow-ups cost cents — the model
and its exceptions are in
[hookprobe/docs/operating.md](../../hookprobe/docs/operating.md#from-a-chat-thread-one-topic-one-run-one-engine-session).

The bridge decides only by **structure** — reply or not, mention or not, which
chat, which sender type. It never reads the text; it carries it. The text is
read by the investigator, under the same read-only posture and guards as an
alert, and only for senders in `HOOKPROBE_FOLLOW_UP_SENDERS`
([hookprobe/docs/configuration.md](../../hookprobe/docs/configuration.md)).

## Two ways to deliver

**As the application** (default): lark-cli, a chat id, buttons that call back,
replies in threads, a message id back for every card. Needs the Lark app below.

**Through a custom bot's webhook** (`LARK_WEBHOOK_URL` set, no app): the same
protocol on the pipe's side; this bridge renders the card and POSTs it to the
bot's incoming URL with the bot's own `{timestamp, sign}` when
`LARK_WEBHOOK_SECRET` is set. No lark-cli, no chat id, no events consumed. What
the platform does not allow is said, not faked: no message id comes back,
`reply_to`/`chat_id` are ignored with a log line, and actions are rendered as
**links** to the pipe's confirm page when the envelope names
`action_link_base` — never as buttons that do nothing. The quickstart runs the
bridge this way against its sink; production runs a second instance this way
for the operator's personal watch bot.

## What it refuses, and what it does not keep

- **Chats.** It posts into, and forwards from, exactly `LARK_CHAT_ID` plus
  `BRIDGE_CHAT_IDS`. A card for any other chat is refused with a 400; a message
  from any other chat is dropped. The blast radius is written here, not decided
  by callers.
- **Cards.** With `BRIDGE_INBOUND_SECRET` set (the channel's `secret`, same
  value), a protocol card must carry the pipe's `X-Hook-Timestamp` /
  `X-Hook-Signature` headers over the exact bytes; a legacy Feishu-shaped body
  must carry the custom-bot `{timestamp, sign}`. Unsigned, wrong or older than
  five minutes → 401, which the pipe records as a failed delivery and retries
  or dead-letters in the open.
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
| `LARK_APP_ID`, `LARK_APP_SECRET` | *(required in app mode)* | the app; the entrypoint writes them into lark-cli's config at first start of each container — see *Switching apps* |
| `LARK_WEBHOOK_URL`, `LARK_WEBHOOK_SECRET` | *(empty = app mode)* | webhook mode: post rendered cards to this custom-bot URL, signed with the bot's secret if it has one |
| `LARK_BRAND` | `lark` | `lark` or `feishu` |
| `LARK_CHAT_ID` | *(required in app mode)* | the default chat: cards without `chat_id` go here, and replies from here are forwarded |
| `BRIDGE_CHAT_IDS` | *(empty)* | comma-separated further chats this bridge may post into and forward from (`options.chat_id` on a pipe channel names one) |
| `BRIDGE_INBOUND_SECRET` | *(empty = accept every card)* | the pipe's channel secret, verified on protocol cards (headers) and legacy cards (body); set it — the port sits on a network shared with other stacks |
| `RELAY_ACTION_URL` | `http://hookrelay:8100/card-action` | where a button press goes |
| `RELAY_THREAD_URL` | *(empty = do not listen for messages)* | the pipe's `lark-thread` door, e.g. `http://hookrelay:8100/hook/lark-thread` |
| `THREAD_SECRET` | *(empty)* | signs message forwards for that door (`X-Hook-Timestamp`, `X-Hook-Signature` = hex HMAC-SHA256 over `"{ts}.{body}"`) — the door's `${LARK_THREAD_SECRET}` |
| `BRIDGE_PORT` | `9100` | where the pipe posts cards |
| `BRIDGE_IDLE_RECYCLE_SECONDS` | `21600` (6h) | after this long with no event on ANY stream, stop the bus so the consumers rebuild it — see *Preventive recycling*; `0` = off |

The image pins `@larksuite/cli` (`ARG LARK_CLI_VERSION`).

**The bridge keeps nothing.** lark-cli's state lives in the container's own
writable layer and is rebuilt from the environment whenever the container is
recreated. There used to be a `/config` named volume here and an
`ENV LARK_CLI_HOME=/config` to point at it; on 2026-09-08 the volume was found
**empty after weeks of running** — lark-cli 1.0.88 ignores `LARK_CLI_HOME` and
writes `$HOME/.lark-cli` regardless. Both are gone rather than repointed,
because what is in that directory is a credential the entrypoint re-derives
from `LARK_APP_ID`/`LARK_APP_SECRET`, a 28-byte version-check cache, and a
`bus.pid` / `bus.sock` / `bus.alive.lock` set belonging to the process that is
running right now. There is no event cursor. Carrying the last three into a new
container is not persistence, it is handing a fresh process the corpse of the
old one's bus — the exact shape of "another event bus is already connected"
this deployment spent a day chasing.

## Wire shapes

The three shapes are the protocol's, defined once in
[docs/bridge-protocol.md](../../docs/bridge-protocol.md): a card model in
(answered with `{"ok": true, "message_id": "om_…"}`), a message out to
`RELAY_THREAD_URL`, a press out to `RELAY_ACTION_URL` as
`{"hookrelay_action": "<token>", "actor": "ou_…"}`. The examples under
[`contract/`](contract/) are what the tests on both sides use;
`outbound-card.json` is produced by the pipe's own builder. Header
`X-Hookstack-Dry-Run: 1` makes this bridge render and answer without sending —
the way to prove the wiring from inside a deployment.

A fourth, optional shape answers **has anybody opened these**: the pipe posts
`{"protocol": …, "read": {"message_ids": […]}}` to the same URL and gets back a
reader count and a first-read timestamp per id. Counts and times, never who —
the identities are the part a pipe's ledger has no business accumulating. It
needs `im:message:readonly`, which the scope list above already includes, and it
answers `{"ok": true, "supported": false, "read": {}}` in webhook mode, where a
custom bot never had a message id to ask about. Twenty ids per request, because
each one is an API call.

## Operating it

- **Is it connected?** `docker exec <bridge> lark-cli event status` → `Bus:
  running`, `Active consumers: 2` (presses and messages). The log shows
  `listening for events (key=…)` for each, and `remote connection check:
  online_instance_cnt=0` on connect.
- **Preventive recycling.** The bridge does not hold the long connection —
  lark-cli's `event _bus` daemon does, and the two `event consume` processes
  attach to it over a unix socket. So a bus whose socket has silently died looks
  from in here exactly like a quiet night: nothing ended, no line arrives, and
  the reconnect loop below never fires because it only fires when a process
  *ends*. There is no keepalive line to count and no ping RTT to read.

  So after `BRIDGE_IDLE_RECYCLE_SECONDS` with no event on **either** stream, the
  bridge runs `lark-cli event stop --force` and lets its own reconnect loop
  rebuild everything. Measured on the work deployment on 2026-09-09: both
  consumers ended within 250 ms, the app's connection slot was already free
  (`online_instance_cnt=0`), a fresh daemon was up 7 s after the stop and the
  second consumer had reattached by 10 s.

  This is **prevention, not detection** — it cannot tell a zombie from a
  weekend, so most recycles are ones it did not need, and the cost of each is
  that ~10 s window plus the risk of finding the app's one slot briefly still
  held (then it is the 60 s backoff below). That is why the default is hours.
  Idleness is measured across both streams together because they share one
  connection: production's press stream went 28 h with zero presses while the
  message stream took twelve, and per-stream idleness would have recycled a
  provably healthy bus nightly.

- **"another event bus is already connected to this app (1 remote event
  connection)"** — something else holds this app's one connection: another
  bridge, a laptop's `lark-cli event consume`, another deployment. Not a
  version problem, not this container. Find and stop the holder, or give this
  bridge its own app. The bridge keeps retrying (60 s backoff) and takes the
  slot when it frees.
- **Switching apps.** Change `LARK_APP_ID` / `LARK_APP_SECRET` in `.env` and
  **recreate** the container (`docker compose up -d`): a new container starts
  with no config and the entrypoint writes the new app's. A bare
  `docker restart` is not enough — it reuses the writable layer, where the old
  config still is, and the entrypoint leaves an existing one alone; there,
  `docker exec <bridge> lark-cli config remove` first. Add the new bot to every
  served chat before either, or cards fail with `Bot/User can NOT be out of the
  chat` until somebody does.
- **Is the wiring right?** From the pipe's container, post a signed protocol
  card with `X-Hookstack-Dry-Run: 1`: a 200 with `rendered` proves the
  address, the secret and the renderer without a message reaching anyone.
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
