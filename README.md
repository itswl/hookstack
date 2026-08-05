# hookrelay

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

## The three built-in gates, in order

| # | gate | skip_code | why this order |
|---|------|-----------|----------------|
| ① | fingerprint dedup (per-source window) | `duplicate` | repeats must not reach the silence check, let alone a channel |
| ② | silence (source-scoped or `*`, with expiry) | `silenced` | quiet is an operator decision and costs no route walk |
| ③ | route matching (priority order, `stop` supported) | `no_route` | an unclaimed event is a *named* outcome, not a mystery |

Delivery is a separate ledger: an outbox row per (event × channel), retried
with exponential backoff (30s·2ⁿ, cap 10 min, 8 attempts) into a visible dead
state. Per-channel `max_per_minute` **defers** — pushback is scheduling, not
failure, so it burns no attempt.

## Configuration

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

## Tests

```bash
.venv/bin/pytest -q
```

Gates order and trace shape, route semantics, per-channel wire formats
(including DingTalk/Feishu signing), backoff → dead-letter, rate-limit
deferral, and the HTTP surface with real signatures.
