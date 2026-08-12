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
    storm_threshold: 60                # storm FUSE: arrivals per window; 0 = off
    storm_window_seconds: 60
```

**The storm fuse is volume, not content** — it catches the high-cardinality
flood that dedup structurally cannot (every payload different, so no
fingerprint repeats). Two stages, one knob:

| stage | trigger | behaviour |
|---|---|---|
| soft | count > `storm_threshold` | event RECORDED (`skipped · storm_suppressed`, window count in its trace), no pipeline, no channel |
| hard | count > 10 × threshold | HTTP 429, nothing stored — at this volume the ledger itself is what needs protecting |

Counters per source appear under `fuse` in `/status` (absent when healthy).
The window is process-local by design: a restart resets it, which is correct
for a fuse — it protects, it does not account.

**When is it mandatory?** Whenever the thing behind the relay has no ingress
backpressure of its own. A comprehensive platform brain usually has one (its own
storm gate), so a deploy in front of one can run without a fuse; **a lite
brain does not** — put a fuse on every door in front of it. Signature verification always
runs first, so an unsigned flood is rejected as unauthenticated and never
consumes fuse budget.

Path syntax: dots walk objects, integers walk arrays — `{alerts.0.labels.alertname}`.
A missing path renders as `""` (an empty title is recoverable; a dropped event is not).

### One door, many payload shapes — named templates

A door often faces more than one sender (the reference production `inbound`
door takes Grafana alerts and SNS relays through the same public URL). Give it
an ORDERED list of named templates, each with an optional selector over the
RAW payload; first match wins, and **the last one must have no selector** —
it is the fallback, and config refuses a list that could leave a payload
unclaimed.

```yaml
templates:
  - name: grafana-in
    kind: extract                  # the only kind today; render is additive later
    match:
      exists: [evalMatches]        # every path must be present
    title: "{title}"
    body: "{message}"
    level: "{state}"
    level_map: {alerting: high}
    fields: {metric: "{evalMatches.0.metric}"}
  - name: sns-in
    match:
      exists: TopicArn             # a bare string works too
      equals: {Type: Notification} # path must render to this value (AND)
    title: "{Subject}"
    body: "{Message}"
  - name: catch-all                # no match = fallback, must be last
    title: "{title}"

sources:
  - name: inbound
    templates: [grafana-in, sns-in, catch-all]
```

**Which template read the event is recorded in the decision trace** (`{"gate":
"extract", "template": "sns-in"}`) — "why is this title empty" has to be
answerable from the ledger, not by re-deriving the payload by hand.

Templates define the vocabulary routing speaks: a field only one template
extracts can still drive a route, and events read by the other templates
simply miss it. Field names may not collide with `source` / `level` / `title`
(the routing context merges fields last, so a collision would silently shadow
a routing key) — config refuses it.

The single-shape inline form (`title:`/`body:`/`level:` directly on the source)
stays valid forever; it becomes a one-entry list called `inline`.

**Custom adapter** (different signature dialect / payload shape): drop a file
in `data/plugins/`, reference by name. `examples/plugins/github_source.py` is
a complete one — copy it in and write `adapter: github`; the door then expects
GitHub's `X-Hub-Signature-256`.

## 2. Processors — `pipeline`

Omit the key entirely → default `[dedup, silence, routes]` (the STANDALONE
posture: dedup here is a storm fuse for deployments with no brain behind the
relay). Write it → the walk is exactly your list, in order. **Must contain
`routes`.**

**Paired with a comprehensive brain?** Use `pipeline: [silence, routes]`
— the brain owns dedup/noise judgment and its accounting must stay truthful;
the edge keeps only the valve. This is how the reference production deploy
runs.

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

### Four processing topologies — pick by SPEED

| | shape | when |
|---|---|---|
| **A. inline fast** ✓ | `http` stage: call out, wait, apply `pass`/`drop`/`set` | sub-second work — scoring, tagging, allowlist checks |
| **B. inline slow** ✗ | the same, with a big timeout | **never**: the stage holds the sender's connection, so the sender times out and RETRIES — you get duplicate alerts while the first copy is still being analysed. `timeout_seconds` above 10 is refused at config load |
| **C. async handoff** ✓ | deliver TO the brain as a channel; it re-enters through its own door | anything slow. This is how a 47-second AI analysis is wired in production |
| **D. park and resume** ✗ | hold the event, ask, continue the pipeline later | not built: the relay would hold in-flight state and start becoming a workflow engine, and "is a parked event accounted for?" pollutes the ledger's promise. C covers the same need |

Topology C splits one logical alert into two ledger events (inbound, and the
brain's return). To make the round trip findable, every outbound delivery
carries `X-Hook-Correlation-Id` (and `X-Request-Id`, which allowlist-minded
receivers actually keep). A brain that quotes it back — e.g. in a field the
return door extracts as `correlation_id` — gets a `{"gate": "correlate",
"with": "hr-86"}` step in the return event's trace.

### Fan out to several brains, then compare

The relay can hand the SAME alert to more than one processing system and
gather what each made of it. Because the input is byte-identical, the
differences in what comes back are differences in JUDGEMENT — which is the
point of running two brains side by side.

```yaml
routes:
  - {name: fan-to-brains, source: inbound, send_to: [to-brain-a, to-brain-b], stop: true}
  - {name: a-to-chat,     source: a-notify,    send_to: [ops-feishu], stop: true}
  # The shadow brain is gathered and compared, never delivered onward —
  # comparing two brains must not double every notification an operator gets.
  - {name: lite-compare,  source: lite-notify, send_to: [compare-log], stop: true}
```

**The correlation contract.** Every outbound delivery carries
`X-Hook-Correlation-Id: hr-<event_id>` (and `X-Request-Id`). A brain that
quotes it back — in a field the return door extracts as `correlation_id` —
gets its reading gathered under the original alert. A brain that cannot quote
it still lands in the ledger; it just is not grouped. Contract, not
requirement.

`GET /trace/{event_id}` assembles the group from either end (ask about a
return, get the same view), with each brain's **latency** — the number that
answers "what does the slow one buy us":

```json
{"origin": {"id": 86, "title": "示例充值超500告警", "deliveries": [...]},
 "returns": [{"fields": {"brain": "brain-lite"}, "level": "medium", "latency_seconds": 0.4},
             {"fields": {"brain": "brain-full"}, "level": "high",   "latency_seconds": 47.0}]}
```

The status page shows it under each event ("看这条的往返 / 各系统加工").

Two standing limits on processors, both deliberate:

- they may only **set fields and pass/drop** — never pick channels, split an
  event, or hand back a rendered payload. Routes stay the only authority. Need
  to influence routing? Write a field and let a route match it.
- multiple brains **chain** (N `http` stages, order is semantics) but do not
  run in parallel.

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

  - name: platform                     # feed a brain downstream, zero code
    type: generic                      # canonical JSON bytes, HMAC over EXACT wire bytes
    url: https://your-platform/v1/webhook
    secret: ${PLATFORM_SECRET}
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
    send_to: [platform]
    priority: 0
```

An event matching several routes goes to the UNION of their channels (each
channel once). No route matched → the decision records `skipped · no_route` —
a named outcome, visible on the page, not a mystery.

Delivery semantics (all channel types): outbox row per (event × channel),
exponential backoff 30s·2ⁿ capped at 10 min, 8 attempts, then a visible
`dead` with the last error. Feishu/DingTalk/WeCom in-body error codes
(`code`/`errcode` ≠ 0) count as failures even on HTTP 200.

### Raw mode — the transparent-edge split

Every channel type accepts:

```yaml
options:
  payload: raw          # deliver the ORIGINAL inbound payload, not the summary
  payload_path: card    # optional: a sub-object of it (dotted path)
```

Two recipes this enables:

**Transparent edge (monitoring → hookrelay → your platform)** — the platform
unchanged, its own per-ecosystem adapters keep working; hookrelay adds the ledger, storm
dedup and a named door in front:

```yaml
channels:
  - name: to-platform
    type: generic
    url: https://your-platform/v1/webhook/grafana   # the platform's per-source ingest
    secret: ${PLATFORM_SECRET}
    signature_header: X-Webhook-Signature
    options: {payload: raw}
# edge dedup must not swallow recoveries: put the state in the fingerprint
sources:
  - name: grafana
    fingerprint_fields: [title, state]
```

**Finished payloads out (brain → hookrelay → 飞书)** — the brain builds
the exact message (interactive cards with their callback buttons survive);
hookrelay owns retry / rate limit / dead letters and injects bot signing only:

```yaml
sources:
  - name: brain-out
    secret: ${WW_OUT_SECRET}
channels:
  - name: feishu-final
    type: feishu
    url: ${FEISHU_WEBHOOK_URL}
    secret: ${FEISHU_WEBHOOK_SECRET}
    options: {payload: raw, payload_path: notification}
routes:
  - {name: brain-to-feishu, source: brain-out, send_to: [feishu-final]}
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
