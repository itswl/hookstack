# hookrelay

[![ci](https://github.com/itswl/hookstack/actions/workflows/ci.yml/badge.svg)](https://github.com/itswl/hookstack/actions/workflows/ci.yml)

The pipe. Part of [hookstack](../README.md); the brain is `hookjudge/`
alongside it. Runs standalone all the same — its gate, Dockerfile and CI
workflow are its own.

Receive webhooks. Decide. Fan out to channels. Nothing else.

A pluggable router (under 6,050 source lines, five dependencies) that takes JSON
webhooks in at one door, walks each event through three named gates, and delivers
to a chat bridge / generic HTTP (DingTalk and WeCom as shipped plugins) — with retries, per-channel rate
limits, and a dead-letter queue you can see.

Both numbers are **budgets, not descriptions**. 6,050 source lines is the
ceiling and five dependencies is the count; `scripts/assert_weight.py` enforces
the first alongside the other stack checks, and crossing it is meant to cost a
conversation rather than a commit. Tests are counted and printed but never capped
— a ceiling that punished tests would make deleting one the cheapest way to land
a feature. The budget is stated rather than measured because the measurement was
what failed: this sentence read "~1400 lines with tests" from 2026-08-05 until
2026-08-20, by which point the source alone was 4,226.

**What it deliberately is not**: an alerting system. No AI, no incidents, no
SLA, no on-call. If an event needs *judgement*, put a brain (hookjudge, or any
comprehensive platform) behind the generic channel. hookrelay only promises two things:

1. **Every event leaves exactly one decision record** saying what happened and
   why — `routed` to which channels, or `skipped` with a named code
   (`duplicate` / `silenced` / `no_route`), plus the ordered gate steps.
2. **Every accepted delivery ends in exactly one of `sent` or `dead`** — with
   attempt count and last error kept in the open, never silently dropped.

## Run

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp config.example.yaml config.yaml   # edit sources/channels/routes
GRAFANA_HOOK_SECRET=xxx BRIDGE_SECRET=xxx \
  .venv/bin/python -m hookrelay      # listens on 127.0.0.1:8100
```

Or run all of hookstack — pipe, brain, a readable downstream and a stub model
— with `docker compose up -d --build`. See [STACK.md](../STACK.md).

## Send something

```bash
BODY='{"title":"db down","message":"primary unreachable","state":"alerting"}'
SIG=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$GRAFANA_HOOK_SECRET" | awk '{print $2}')
curl -s http://127.0.0.1:8100/hook/grafana \
  -H "content-type: application/json" -H "X-Hook-Signature: $SIG" -d "$BODY"
```

The response IS the decision trace:

```json
{"event_id": 1, "outcome": "routed", "channels": ["ops-feishu"],
 "steps": [{"gate": "dedup", "result": "pass"},
           {"gate": "silence", "result": "pass"},
           {"gate": "routes", "considered": [...], "matched_channels": ["ops-feishu"]}]}
```

## Architecture: a skeleton with three sockets

```
 upstream                     pipeline                          downstream
┌──────────────┐   ┌──────────────────────────────┐   ┌──────────────────────┐
│ source       │   │ dedup → silence → … → routes │   │ channel types        │
│ ADAPTERS     │ → │ PROCESSORS (ordered, config) │ → │ bridge generic       │
│ default,     │   │ built-in: dedup silence      │   │ + plugins            │
│ github, …    │   │ routes set filter http       │   │ + your plugin        │
└──────────────┘   └──────────────────────────────┘   └──────────────────────┘
```

All three are registry names. Built-ins register through the same decorators
a plugin uses; plugins are plain `.py` files in `plugins/` (HOOKRELAY_PLUGINS),
imported at startup **before** config validation — an unknown name fails the
boot, never the first event. Tested examples live in `examples/plugins/`.

```python
# plugins/pagerduty_channel.py — a complete custom channel
from hookrelay import registry


@registry.channel("pagerduty")
def build(channel, message, now):
    return channel.url, {"summary": message["title"], "severity": message["level"]}, {}
```

### The pipeline is config

```yaml
pipeline:
  - dedup
  - silence
  - type: http          # hand the event to an external brain
    name: triage
    url: ${BRAIN_URL}
    headers: {authorization: Bearer ${BRAIN_KEY}}
    timeout_seconds: 3
    on_error: pass      # fail-open (or `drop` to fail closed)
    when: {source: probe-notify}   # optional: put the stage on ONE lane
  - type: filter
    name: mute-low
    when: {level: [low]}
    skip_code: low_muted
  - routes
```

Order is the point: dedup **before** the brain dedups on raw titles; dedup
**after** it dedups on rewrites. Every stage appends its step to the trace.

### The `thread_lookup` processor

A person replies under a card the pipe sent; an IM bridge forwards the reply
through a signed door with the platform id of that card. This stage asks the
ledger which delivery had that id, which chain it belongs to and which
investigation session the chain carries, and writes `fields.session`,
`fields.thread_root`, `fields.kind: follow_up` and a correlation quote onto the
event — so the reply lands in the same chain and a route can hand it to the
investigator addressed. A reply under a card nobody here sent is skipped with
`skip_code` (default `unknown_thread`), recorded, never routed. The text is
carried, not read.

```yaml
  - type: thread_lookup
    name: thread-lookup
    when: {source: lark-thread}
    from: root_message_id        # the field holding the platform id of the card replied under
    skip_code: unknown_thread
```

A top-level @-message opens a topic: the bridge roots it at itself and marks
`topic: new`. With no card behind it the stage would skip it, unless the
deployment says what a new topic starts — `on_new_topic: {kind: task, level:
high}` shapes it as a work item for the planner, `{kind: brief, level: high}`
as a question for the investigator — and writes `fields.thread_root` (the
person's message) so the report lands inside the topic. A later reply in that
topic has the person's message as its root, not a card; the lookup then finds
the chain through the report that carried the same `thread_root`.

`on_new_topic` covers a second case that looks different and is the same: a
reply under a card this pipe DID send, whose chain carries no investigation —
a watcher's notification, a verdict nobody investigated. The stage resolves the
chain, finds no session, and shapes the message as a question rather than as a
follow-up to nothing, adding `fields.about` (what the card was about, since a
person replying under a card does not repeat its contents). Without it the
message keeps `kind: follow_up`, which either matches no route or reaches a node
whose answer is "no investigation behind this thread" — both of which reach a
ledger and not the person who asked. Route on `kind` rather than on `topic` to
catch both shapes.

The other half is on the channel: `options: {thread_replies: true}` on a `bridge`
channel makes a delivery whose event carries
`fields.thread_root` go out as a reply in that thread (`reply_to` in the body
the bridge reads), and `options: {chat_id: oc_…}` names the chat, so one bridge
serves several channels (the bridge refuses a chat it was not configured for).
The stage also writes `fields.return_source` — the door the session's report
came through — so a deployment with several investigators routes the reply to
the node that holds the session (`when: {return_source: plan-notify}`). The
ledger keeps the platform's message id of every sent delivery
(`platform_message_id`) for exactly this lookup.

### The `http` processor contract

Request `POST url`:
```json
{"source": "grafana", "event": {"title": "...", "body": "...", "level": "high", "fields": {}}, "received_at": 1700000000.0}
```
Response:
```json
{"action": "pass" | "drop", "skip_code": "optional-name", "set": {"level": "high", "fields": {"scored_by": "brain"}}}
```
Timeout / non-2xx / bad JSON → the stage's `on_error` policy, recorded in the
trace either way.

`when` takes the same conditions as a route (`source`, `level`, `title`, any
field) and is what makes an external decider **placeable**: without it the stage
fires on every event, so the only way to scope one was to teach the node every
source name in your config — which a node somebody else wrote cannot know, and
which turns every new lane into an edit to their service. Off-lane events record
a `not_applied` step and never reach the network.

### Pairing a comprehensive brain with hookrelay

Two shapes, both zero-code:
- **A platform as downstream brain** (async): a `generic` channel with the
  receiver's `signature_header` and secret posts the
  normalized event straight into its ingest — hookrelay fans out fast, the
  heavy analysis happens over there.
- **Any scorer as a pipeline stage** (sync): the `http` contract above; point
  it at anything that answers within the timeout.

## Product doctrine: a content-blind pipe

One test decides what belongs here: **is this a property of a good PIPE, or a
judgment about the alert's worth?** Pipe properties live in hookrelay.
Judgment belongs to a brain behind it (or nowhere).

Four pillars — the product itself:

| pillar | what it owns |
|---|---|
| receive | doors, signature dialects, extraction for routing |
| route | source + conditions → channels, priority, stop |
| deliver | retry, backoff, rate limits, dead letters, channel wire formats |
| account | one decision per event, one outcome per delivery — the soul |

Pipe *protections* — kept, but named for what they are:

- **silence is a VALVE**, not noise reduction: the emergency shutoff an
  operator pulls when the thing behind the pipe is melting. Source-scoped or
  global, always with expiry.
- **the STORM FUSE is volume protection**: per-door arrival limits
  (`storm_threshold`), catching the high-cardinality flood that content dedup
  structurally cannot. Soft stage keeps the account, hard stage (10×) protects
  the account. Mandatory in front of anything without its own backpressure.
- **dedup is CONTENT protection**, not noise judgment: identical payloads
  inside a window. In a brain-paired deployment turn it OFF
  (`pipeline: [silence, routes]`) so the brain's own noise accounting stays
  truthful; the fuse is the one that stays.
- **rate limits protect downstream quotas** by deferring, never dropping.
- **fold is PACING on a return door**: one card per condition per window for
  verdicts the brain already judged worth a person, the repeats recorded as
  folded into the card they repeat. It drops where a rate limit defers, which
  is why it is pinned to a return door by name and never stands in front of a
  brain; the number it answers to is on file
  (`.agents/notes/implemented/2026-09-28-one-card-per-condition-per-hour-on-the-return-door.md`).

Judgment features (`filter`, `set`, dedup-as-noise-control) exist for
**standalone posture** — a small team with no brain that still wants
webhook→Feishu with taste. In **paired posture** (a comprehensive brain behind the
relay) they should all yield; the `http` processor is the doorway that keeps
it honest — judgment gets *delegated*, never absorbed.

| | paired posture (with a brain) | standalone posture |
|---|---|---|
| pipeline | `[silence, routes]` | default `[dedup, silence, routes]` (+ filter/set to taste) |
| templates' job | extract enough to route + a readable ledger title | full message formatting |
| content | blind both ways (raw in, raw out) | the templates ARE the presentation |

Delivery is a separate ledger: an outbox row per (event × channel), retried
with exponential backoff (30s·2ⁿ, cap 10 min, 8 attempts) into a visible dead
state. Per-channel `max_per_minute` **defers** — pushback is scheduling, not
failure, so it burns no attempt.

## Configuration

Full field-by-field reference: **[docs/configuration.md](docs/configuration.md)**.

One YAML file (see `config.example.yaml`): `sources` (who may knock, how to
extract `title`/`body`/`level`/`fields` via `{dotted.paths.0.into.json}`),
`channels` (`bridge` for people, `generic` for machines, each with optional signing and
rate limit — `bridge` sends a card model to a chat sidecar, [docs/bridge-protocol.md](../docs/bridge-protocol.md); DingTalk and WeCom markdown come from a shipped plugin), `routes` (match on source + extracted fields → channels).
Secrets are written as `${ENV_NAME}` and resolve at startup; the file itself
stays commit-safe.

Operational knobs are environment variables (`HOOKRELAY_*`). Every one of them,
with defaults: **[docs/reference.md](docs/reference.md)**, generated from
`hookrelay/settings.py`.

## API

| method | path | auth |
|---|---|---|
| POST | `/hook/{source}` | per-source HMAC (`X-Hook-Signature`) |
| GET | `/status` | `X-Read-Token` (open if unset — dev mode) |
| POST | `/silences` `{source:"*"|name, minutes, note}` | `X-Admin-Token` (endpoint disabled if unset) |
| DELETE | `/silences/{id}` | `X-Admin-Token` |
| GET | `/healthz` | none |

The two header names above come from two environment variables with opposite
behaviour when unset, and the difference is deliberate:

| variable | unset means |
| --- | --- |
| `HOOKRELAY_READ_TOKEN` | the read guard is **off** — dev mode, the board answers anyone who reaches the port. Set it before anything proxies this service, or the whole ledger is public |
| `HOOKRELAY_ADMIN_TOKEN` | the admin endpoints **refuse everyone**. `token_ok` returns False on an empty configured value, so an unconfigured instance cannot be muted or reconfigured by whoever finds the port |

Same helper, opposite semantics, chosen by the caller. Read is convenient when
empty; admin is inert when empty, because `PUT /config` can rewrite where every
alert goes.

## Operating it

| surface | what it answers |
|---|---|
| `GET /sw.js` | the board's service worker — one file in three services: the page and the icons offline, and never the data — every read still needs the token, and nothing from `/status` or `/live` is cached, so offline the board says the pipe is out of reach rather than showing a stale one as current. Served at the page's own level so its scope is the board, and no-cache so a new page is never pinned behind an old worker |
| `GET /static/…` | the web manifest and icons that make the board an app on a phone. The manifest's start URL and scope are relative, so a board served under a path prefix installs under it |
| `GET /` | the board, read like an inbox: one sentence on whether anything is waiting on you, then one row per alert with its status in words and its seven stages (received · judged · notified · investigated · a person · condition · fix); a row opens the alert's whole story — every delivery with a retry for the dead ones, the bytes of both directions, the audit record — from any handle; the ledger of every event with its decision chain; deliveries, silences, and routing with the config editor and a dry run; Chinese or English, light or dark following the system |
| `GET /status?q=&source=&outcome=&before_id=&limit=` | the same as JSON (read token) |
| `GET /live` | the board's wake-up line — NDJSON, one `changed` per burst of ledger writes, so the page needs no clock (read token) |
| `GET /timeline` | what happened, as one stream — chains gathered by correlation, newest first, with what each chain spent. `/status` answers "recently" and `/trace` answers "this one"; this is the one that answers "what happened", after a review took five endpoints across two machines joined by eye. A projection of the ledger, not a second one: nothing new is asked of any node, because a node here may be somebody else's and a store it had to write to would take the replaceable node with it. Cost appears per hop where a return door extracts `meta.cost_usd` into a field; `unpriced_hops` counts the rest, since a free hop and an unpriced one are different facts. It also groups chains that share a judge-sent `burst_id` into **incidents** — the operator's unit, so five cards for one root cause count as one interruption rather than five chains. The pipe reads that grouping, never computes it: which alerts are one incident is a judgement about content, and the pipe stays content-blind (read token) |
| `GET /audit/{event_id}` | one operation as an **accountability record**: every hop of the chain (followed to its root), every delivery and return with its cost, every human press — with bodies replaced by sha256 + size, because an auditor needs proof the bytes on file are the bytes that left, not the payloads themselves; `/trace` holds the bytes behind each digest. `scripts/audit_export.py` renders it, with the investigator's own audit, as Markdown (read token) |
| `GET /trace/{ref}` | one alert's whole journey, from **any handle it left behind** — an event id, the `hr-<id>` the pipe stamps on egress, a session key or work id a return door extracted, or the platform id of a card the pipe sent; each one minted or copied by the pipe, so the lookup reads identifiers and never content. The original payload as received, every delivery with the exact body that left the socket (`sent_body`; body only, never headers, and never a live action token), what each brain sent back, what a person pressed, and what the condition did **afterwards** (`recoveries`, `refires`: the same source and title within a day). An alert's row on the board opens it, top to bottom (read token) |
| `GET /metrics` | Prometheus text: events by door/outcome, deliveries by channel/result, outbox depth, fuse and silences (read token) |
| `POST /explain/{source}` | dry run — what WOULD this payload do; records nothing, delivers nothing, calls no brain (admin token) |
| `GET /topology` | the whole graph from config alone — doors, stage placements, exits, who feeds whom, and the hazards the shape implies (a door no route matches, an exit no route feeds, a door that can fall through to a wildcard). Pure: no store, no network, no clock. Channel URLs are printed as `scheme://host:port` only — a webhook URL is its own credential (admin token) |
| `GET/PUT /config`, `POST /config/reload` | the config file, validated-or-nothing, hot-applied (admin token); a `PUT` onto a config mounted read-only — every compose here mounts it so — answers 409 with what to do instead and changes nothing |
| `POST /silences`, `DELETE /silences/{id}` | the valve (admin token) |
| `POST /deliveries/{id}/retry` | a dead letter's second chance (admin token) |
| `POST /card-action` | a button pressed on a notification card — authorised by the signed single-use token in the button, not by the caller |

Environment knobs beyond the doors: `HOOKRELAY_RETENTION_DAYS` (14),
`HOOKRELAY_ALARM_URL` + `HOOKRELAY_ALARM_MIN_INTERVAL_SECONDS` (dead-letter
self-alarm), `HOOKRELAY_BREAKER_THRESHOLD` / `_COOLDOWN_SECONDS`,
`HOOKRELAY_MAX_ATTEMPTS`, `HOOKRELAY_PLUGINS`,
`HOOKRELAY_ACTION_SECRET` + `HOOKRELAY_ACTION_TTL_SECONDS` +
`HOOKRELAY_CARD_CALLBACK_SECRET` (card buttons — empty secret means no card
carries one, see [docs/configuration.md](docs/configuration.md)).

## Tests

```bash
bash scripts/gate.sh     # the full gate — the exact list CI runs
.venv/bin/pytest -q      # just the suite
```

A contract test pins gate.sh and ci.yml to the same check list: adding one
without the other fails. CI also builds the image and boots it, because the
container is how this actually ships.

Gates order and trace shape, route semantics, per-channel wire formats
(including the bridge's header signature and a plugin's DingTalk query signing), backoff → dead-letter, rate-limit
deferral, and the HTTP surface with real signatures.
