# Configuration reference

One file — `config.yaml` (in Docker: `data/config.yaml`, mounted at `/data`) —
with four sections: `sources` (upstream), `pipeline` (processors), `channels`
(downstream), `routes` (what goes where). Any value written as `${NAME}`
resolves from the environment at startup; with the shipped compose file the
whole `.env` is injected, so a new secret is one line in `.env`.

Apply changes with `docker compose restart hookrelay`. Validation runs at
boot: an unknown adapter/processor/channel type, a route pointing at a missing
channel, or a pipeline without a `routes` stage **refuses to start** with a
named error (`docker compose logs hookrelay`) — config mistakes must never
wait for the first event to surface.

## 1. Upstream — `sources`

A source is one inbound door: `POST /hook/{name}`.

```yaml
sources:
  - name: grafana                      # → POST /hook/grafana
    secret: ${GRAFANA_HOOK_SECRET}     # HMAC-SHA256 of the raw body,
                                       # X-Hook-Signature header ("sha256=" prefix ok).
                                       # EMPTY = accepted unsigned (trusted networks only).
    adapter: default                   # who verifies + reads; plugins add more (e.g. github)
    title: "{title}"                   # {dotted.path} into the JSON payload
    body: "{message}"
    level: "{state}"                   # rendered, lowercased, then mapped:
    level_map: {alerting: high, ok: info}   # unmapped values pass through; empty → "info"
    fields:                            # extra extracted keys — visible on the page,
      rule: "{ruleId}"                 # usable in route/filter conditions
    fingerprint_fields: [title]        # duplicate identity; empty = title+body
    dedup_window_seconds: 60
```

Path syntax: dots walk objects, integers walk arrays — `{alerts.0.labels.alertname}`.
A missing path renders as `""` (an empty title is recoverable; a dropped event is not).

**Custom adapter** (different signature dialect / payload shape): drop a file
in `data/plugins/`, reference by name. `examples/plugins/github_source.py` is
a complete one — copy it in and write `adapter: github`; the door then expects
GitHub's `X-Hub-Signature-256`.

## 2. Processors — `pipeline`

Omit the key entirely → default `[dedup, silence, routes]`. Write it → the
walk is exactly your list, in order. **Must contain `routes`.**

```yaml
pipeline:
  - dedup                              # bare string = built-in, no options
  - silence
  - type: set                          # static enrichment
    name: tag-env
    when: {source: grafana}            # optional guard
    set: {fields: {env: prod}}
  - type: http                         # hand the event to an external brain
    name: triage
    url: ${BRAIN_URL}
    headers: {authorization: Bearer ${BRAIN_KEY}}
    timeout_seconds: 3
    on_error: pass                     # pass = fail-open · drop = fail-closed
  - type: filter                       # named drop
    name: mute-low
    when: {level: [low]}
    skip_code: low_muted
  - routes
```

`when` conditions (same everywhere — routes, filter, set):

| form | meaning |
|---|---|
| `level: high` | exact match |
| `level: [high, critical]` | membership |
| `title: {contains: 数据库}` | substring |

Keys available: `source`, `level`, `title`, plus every extracted `fields` key.
All conditions in one `when` must match (AND).

**Order is semantics**: `set` before `dedup` changes the fingerprint; `http`
before `routes` lets the brain's rewritten `level` decide the routing. Every
stage appends its step to the trace, so the page shows the walk you configured.

**`http` contract** — request:
```json
{"source": "grafana", "event": {"title": "…", "body": "…", "level": "high", "fields": {}}, "received_at": 1700000000.0}
```
response:
```json
{"action": "pass" | "drop", "skip_code": "optional-name", "set": {"level": "high", "fields": {"scored_by": "brain"}}}
```

**Custom processor**: a plugin file with `@registry.processor("mine")` on a
class exposing `async def run(self, rt, ctx, options) -> ("pass"|"skip", code)`.

## 3. Downstream — `channels` + `routes`

Channels define pipes; routes decide which events enter which pipes.

```yaml
channels:
  - name: ops-feishu
    type: feishu                       # card message; secret = bot signing (optional)
    url: ${FEISHU_WEBHOOK_URL}
    secret: ${FEISHU_WEBHOOK_SECRET}
    max_per_minute: 20                 # rate limit DEFERS (reschedules), never drops

  - name: ops-dingtalk
    type: dingtalk                     # markdown; secret = 加签 (query-string sign)
    url: ${DINGTALK_WEBHOOK_URL}
    secret: ${DINGTALK_WEBHOOK_SECRET}

  - name: ops-wecom
    type: wecom                        # markdown; WeCom bots have no signing
    url: ${WECOM_WEBHOOK_URL}

  - name: webhookwise                  # feed a brain downstream, zero code
    type: generic                      # canonical JSON bytes, HMAC over EXACT wire bytes
    url: https://your-ww/v1/webhook
    secret: ${WW_SECRET}
    signature_header: X-Webhook-Signature   # speak the receiver's dialect

routes:                                # walked by priority, highest first
  - name: high-everywhere
    source: "*"                        # "*" or one source name
    when: {level: [high, critical]}
    send_to: [ops-feishu, ops-dingtalk]
    priority: 100
    stop: false                        # true = stop the walk after this match

  - name: everything-to-archive
    source: "*"
    send_to: [webhookwise]
    priority: 0
```

An event matching several routes goes to the UNION of their channels (each
channel once). No route matched → the decision records `skipped · no_route` —
a named outcome, visible on the page, not a mystery.

Delivery semantics (all channel types): outbox row per (event × channel),
exponential backoff 30s·2ⁿ capped at 10 min, 8 attempts, then a visible
`dead` with the last error. Feishu/DingTalk/WeCom in-body error codes
(`code`/`errcode` ≠ 0) count as failures even on HTTP 200.

### Raw mode — the WebhookWise split

Every channel type accepts:

```yaml
options:
  payload: raw          # deliver the ORIGINAL inbound payload, not the summary
  payload_path: card    # optional: a sub-object of it (dotted path)
```

Two recipes this enables:

**Transparent edge (monitoring → hookrelay → WebhookWise)** — WW unchanged,
its own per-ecosystem adapters keep working; hookrelay adds the ledger, storm
dedup and a named door in front:

```yaml
channels:
  - name: to-webhookwise
    type: generic
    url: https://your-ww/v1/webhook/grafana     # WW's own per-source ingest
    secret: ${WEBHOOKWISE_SECRET}
    signature_header: X-Webhook-Signature
    options: {payload: raw}
# edge dedup must not swallow recoveries: put the state in the fingerprint
sources:
  - name: grafana
    fingerprint_fields: [title, state]
```

**Finished payloads out (WebhookWise → hookrelay → 飞书)** — the brain builds
the exact message (interactive cards with their callback buttons survive);
hookrelay owns retry / rate limit / dead letters and injects bot signing only:

```yaml
sources:
  - name: ww-out
    secret: ${WW_OUT_SECRET}
channels:
  - name: feishu-final
    type: feishu
    url: ${FEISHU_WEBHOOK_URL}
    secret: ${FEISHU_WEBHOOK_SECRET}
    options: {payload: raw, payload_path: notification}
routes:
  - {name: ww-to-feishu, source: ww-out, send_to: [feishu-final]}
```

`payload: raw` with a missing/empty path fails INTO the delivery ledger with a
named error — a misconfiguration must never silently deliver an empty body.

### Retention

`HOOKRELAY_RETENTION_DAYS` (default 14, `0` = keep forever): an hourly sweep
purges events older than the window **whose deliveries are all settled** — a
queued promise is never deleted out from under the worker. Expired silences go
with them.

**Custom channel**: a plugin file with

```python
from hookrelay import registry

@registry.channel("pagerduty")
def build(channel, message, now):
    # message: event_id, source, title, body, level, fields, received_at
    return channel.url, {"summary": message["title"]}, {}
```

`message` is the normalized event; return `(url, dict_or_bytes, headers)` —
return BYTES when you sign, so the signature covers exactly what is sent.
