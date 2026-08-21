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
    # Optional: truthy rendering marks the event a recovery (top-level
    # is_recovery on the outbound event, tri-state: omit = receivers use
    # their own detection). Never a field — fields build identity, and a
    # flag that flips between firing and recovery would split the pair.
    recovery: "{meta.is_recovery}"
    fields:                            # extra extracted keys — visible on the page,
      rule: "{ruleId}"                 # usable in route/filter conditions
    fingerprint_fields: [title]        # duplicate identity; empty = title+body
                                       # names checked AT BOOT (see below)
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

**`fingerprint_fields` is checked against that vocabulary at boot.** A
misspelled name used to resolve to `""` for every event, so every alert of that
source shared ONE fingerprint and all but the first were skipped as
`duplicate` — total alert loss that reads on the board as excellent dedup. The
check is the UNION of every template the door lists, plus `title` / `body` /
`level`, plus any `set` stage standing ahead of the dedup stage that takes the
fingerprint (a field set after it is still empty when identity is decided). A
pipeline whose enrichment cannot be enumerated — an `http` brain, or a plugin
processor — is not judged at all: refusing honest config is worse than missing
a typo.

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
| `title: {contains: database}` | substring |

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
{"origin": {"id": 86, "title": "Single top-up over 500", "deliveries": [...]},
 "returns": [{"fields": {"brain": "brain-lite"}, "level": "medium", "latency_seconds": 0.4},
             {"fields": {"brain": "brain-full"}, "level": "high",   "latency_seconds": 47.0}]}
```

The status page shows it under each event (the round-trip panel).

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
    type: dingtalk                     # markdown; secret = query-string signing
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
# edge dedup must not swallow recoveries: put the state in the fingerprint —
# and a name in fingerprint_fields must be one this door really extracts
sources:
  - name: grafana
    fields:
      state: "{state}"
    fingerprint_fields: [title, state]
```

**Finished payloads out (brain → hookrelay → Feishu)** — the brain builds
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

## 4. The card's way back — `card_actions`

A notification card can carry buttons. The brain **declares** which actions its
result deserves (that is judgement — see the processed-event contract); this
block decides which of those a deployment actually offers, and where a press
goes. A kind that is absent here is dropped before a button is minted, so a
brain asking for one is making a request, not a guarantee.

```yaml
card_actions:
  silence:                          # the pipe owns silences — no channel needed
    params: {minutes: 60}           # optional: override what the brain asked for
  followup:
    forward_to: probe-action        # a channel; must exist
  approve:
    forward_to: probe-action
  useful:
    forward_to: judge-feedback
  useless:
    forward_to: judge-feedback
  remember:
    forward_to: probe-action        # adopt one queued memory line, in one tap
```

Six kinds exist: `silence`, `followup`, `approve`, `useful`, `useless`,
`remember`. Unknown
kinds, a `forward_to` naming no channel, and any non-`silence` kind without a
`forward_to` all fail **at boot** — a button that 404s when an operator finally
presses it is worse than no button.

### Buttons where a platform calls back, links where it cannot

Only Feishu posts a callback. DingTalk and WeCom webhook robots cannot — their
ActionCard buttons are URL jumps — so on those channels an action is rendered as
a **link** instead, and that needs `HOOKRELAY_PUBLIC_URL` to point somewhere the
operator's browser can reach. Empty means those channels carry no actions at all,
which is the honest default: a link nobody can reach is worse than no link.

The link lands on `GET /card-action`, which **performs nothing** — it shows a
Confirm button whose POST is the real action. That indirection is not politeness:
chat clients fetch links to build previews, so a GET that silenced an alert would
fire when the card was rendered rather than when a person decided.

This also matters for the `escalation` block below, which asks "did any human
touch this?" and has a card action press as its only evidence. On a deployment
where no press can ever happen — no secret, no enabled kind, or no channel that
can carry one — the escalation sweep **disarms itself and says so in the log**,
rather than reading every alert as ignored and escalating all of them.

`HOOKRELAY_ACTION_SECRET` signs the buttons and verifies them on the way back.
**Empty means no card carries an action at all**, which is the right default: an
unsigned button is a URL anyone in the group chat can press on your behalf. A
token is single-use (a second press answers `already_done`) and expires after
`HOOKRELAY_ACTION_TTL_SECONDS` (default 86400) — a card forwarded into a chat is
a token in everyone's scrollback.

Presses arrive at `POST /card-action`. `silence` is performed here; every other
kind becomes an event plus a delivery to its channel, so a forwarded press
inherits the retry, the rate limit, the dead letter and the ledger row. The
receiving door verifies the channel's signature, so **the channel's `secret`
must equal the door's own secret** — the same coupling `to-judge` and `to-probe`
already have. Set `HOOKRELAY_CARD_CALLBACK_SECRET` to additionally require the
family's timestamped signature on the callback itself; useful when a gateway
sits in front of the IM platform.

The forwarded body:

```json
{"action": {"kind": "followup", "params": {"prompt": "Why do you believe that?"}},
 "correlation_id": "hr-11", "event_id": 11, "actor": "ou_z", "at": 1787135929}
```

`actor` is whatever opaque user id the platform sent, never a display name.
Every honoured press is a ledger row, and `GET /trace/{event_id}` returns them
under `human_actions` — so the timeline answers "and what did a person do about
it", not only what the machine did.

## 5. Nobody was awake — `escalation`

An alert can be judged well, dressed well and delivered well, and still be
ignored because it arrived at 3am. This is the answer to that, and it is
deliberately the smallest one: there is no on-call rotation in this family (see
[the decision record](../../.agents/notes/proposed/2026-08-19-nobody-owns-an-alert-and-no-component-picked-it-up.md)),
so instead of asking *who* should have acted it asks whether **anybody** did.

```yaml
escalation:
  after_minutes: 15
  send_to: [pager]                  # channels; must exist
  levels: [critical, high]          # empty = every level, rarely what you want
```

**Off unless configured.** An escalation nobody asked for is a second alert
about the first alert, in the middle of the night.

"Touched" means a card action was pressed — the `card_actions` ledger is the
only evidence the pipe has that a person was there, which is why this could not
exist before the buttons did. A silence counts, a follow-up counts, a "not worth
it" counts: each of them is somebody awake. A press recorded against the
verdict's card (carrying `hr-<event_id>`) counts for the front-door alert it
belongs to, because that is where the button actually lives.

Three conditions before an alert is eligible, each one load-bearing:

| | why |
| --- | --- |
| it was **delivered** | an alert that never left is not unacknowledged, it is undelivered — and the dead-letter alarm already owns that story |
| it is **untouched** | no card action for it, by event id or by correlation id |
| it has **not escalated** | stamped before the second delivery is enqueued, so a cold alert escalates once and not once per worker tick |

The escalation is an ordinary delivery against the **same event**, so it
inherits the retry, the rate limit, the dead letter and the ledger row — and the
board reads it as what it is: this alert, sent somewhere else, later. It is not a
new event, because a second event about the first one makes the ledger lie about
how many alerts arrived.

Unconfigured, misrouted or impossible values fail **at boot**, like every other
name in this file.

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
