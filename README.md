# hookrelay

[![ci](https://github.com/itswl/hookrelay/actions/workflows/ci.yml/badge.svg)](https://github.com/itswl/hookrelay/actions/workflows/ci.yml)

Receive webhooks. Decide. Fan out to channels. Nothing else.

A small, pluggable router (~1400 lines with tests, five dependencies) that takes JSON webhooks in at
one door, walks each event through three named gates, and delivers to Feishu /
DingTalk / WeCom / generic HTTP — with retries, per-channel rate limits, and a
dead-letter queue you can see.

**What it deliberately is not**: an alerting system. No AI, no incidents, no
SLA, no on-call. If an event needs *judgement*, put a brain (like WebhookWise)
behind the generic channel. hookrelay only promises two things:

1. **Every event leaves exactly one decision record** saying what happened and
   why — `routed` to which channels, or `skipped` with a named code
   (`duplicate` / `silenced` / `no_route`), plus the ordered gate steps.
2. **Every accepted delivery ends in exactly one of `sent` or `dead`** — with
   attempt count and last error kept in the open, never silently dropped.

## Run

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp config.example.yaml config.yaml   # edit sources/channels/routes
GRAFANA_HOOK_SECRET=xxx FEISHU_WEBHOOK_URL=https://... \
  .venv/bin/python -m hookrelay      # listens on 127.0.0.1:8100
```

Or `docker compose up -d` with `./data/config.yaml` in place.

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
│ ADAPTERS     │ → │ PROCESSORS (ordered, config) │ → │ feishu dingtalk      │
│ default,     │   │ built-in: dedup silence      │   │ wecom generic        │
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
  - type: filter
    name: mute-low
    when: {level: [low]}
    skip_code: low_muted
  - routes
```

Order is the point: dedup **before** the brain dedups on raw titles; dedup
**after** it dedups on rewrites. Every stage appends its step to the trace.

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

### Using WebhookWise with hookrelay

Two shapes, both zero-code:
- **WebhookWise as downstream brain** (async): a `generic` channel with
  `signature_header: X-Webhook-Signature` and WebhookWise's secret posts the
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
| 接 receive | doors, signature dialects, extraction for routing |
| 路由 route | source + conditions → channels, priority, stop |
| 发 deliver | retry, backoff, rate limits, dead letters, channel wire formats |
| 账本 account | one decision per event, one outcome per delivery — the soul |

Pipe *protections* — kept, but named for what they are:

- **silence is a VALVE**, not noise reduction: the emergency shutoff an
  operator pulls when the thing behind the pipe is melting. Source-scoped or
  global, always with expiry.
- **the STORM FUSE is volume protection**: per-door arrival limits
  (`storm_threshold`), catching the high-cardinality flood that content dedup
  structurally cannot. Soft stage keeps the account, hard stage (10×) protects
  the account. Mandatory in front of anything without its own backpressure —
  WebhookWise-lite, for instance.
- **dedup is CONTENT protection**, not noise judgment: identical payloads
  inside a window. In a brain-paired deployment turn it OFF
  (`pipeline: [silence, routes]`) so the brain's own noise accounting stays
  truthful; the fuse is the one that stays.
- **rate limits protect downstream quotas** by deferring, never dropping.

Judgment features (`filter`, `set`, dedup-as-noise-control) exist for
**standalone posture** — a small team with no brain that still wants
webhook→飞书 with taste. In **paired posture** (a WebhookWise behind the
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
`channels` (feishu / dingtalk / wecom / generic, each with optional signing and
rate limit), `routes` (match on source + extracted fields → channels).
Secrets are written as `${ENV_NAME}` and resolve at startup; the file itself
stays commit-safe.

Operational knobs are environment variables (`HOOKRELAY_*`): DB path, config
path, admin/read tokens, body-size cap, max attempts.

## API

| method | path | auth |
|---|---|---|
| POST | `/hook/{source}` | per-source HMAC (`X-Hook-Signature`) |
| GET | `/status` | `X-Read-Token` (open if unset — dev mode) |
| POST | `/silences` `{source:"*"|name, minutes, note}` | `X-Admin-Token` (endpoint disabled if unset) |
| DELETE | `/silences/{id}` | `X-Admin-Token` |
| GET | `/healthz` | none |

## Operating it

| surface | what it answers |
|---|---|
| `GET /` | the board: queue, breakers, fuse, silences, searchable events with full traces |
| `GET /status?q=&source=&outcome=&before_id=&limit=` | the same as JSON (read token) |
| `GET /metrics` | Prometheus text: events by door/outcome, deliveries by channel/result, outbox depth, fuse and silences (read token) |
| `POST /explain/{source}` | dry run — what WOULD this payload do; records nothing, delivers nothing (admin token) |
| `GET/PUT /config`, `POST /config/reload` | the config file, validated-or-nothing, hot-applied (admin token) |
| `POST /silences`, `DELETE /silences/{id}` | the valve (admin token) |
| `POST /deliveries/{id}/retry` | a dead letter's second chance (admin token) |

Environment knobs beyond the doors: `HOOKRELAY_RETENTION_DAYS` (14),
`HOOKRELAY_ALARM_URL` + `HOOKRELAY_ALARM_MIN_INTERVAL_SECONDS` (dead-letter
self-alarm), `HOOKRELAY_BREAKER_THRESHOLD` / `_COOLDOWN_SECONDS`,
`HOOKRELAY_MAX_ATTEMPTS`, `HOOKRELAY_PLUGINS`.

## Tests

```bash
bash scripts/gate.sh     # the full gate — the exact list CI runs
.venv/bin/pytest -q      # just the suite
```

A contract test pins gate.sh and ci.yml to the same check list: adding one
without the other fails. CI also builds the image and boots it, because the
container is how this actually ships.

Gates order and trace shape, route semantics, per-channel wire formats
(including DingTalk/Feishu signing), backoff → dead-letter, rate-limit
deferral, and the HTTP surface with real signatures.
