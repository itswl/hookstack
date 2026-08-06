# hookjudge

A brain behind a pipe. **It judges; it does nothing else.**

An alert arrives already normalized. hookjudge decides what it means — a
one-line summary, how important it is, what kind of event it is, what it
touches — records that decision with what it cost, and hands it back. It never
learns what Grafana sends or what a Feishu card looks like. Those are the
pipe's job ([hookrelay](https://github.com/itswl/hookrelay)), and keeping them
there is the entire point of this service existing separately.

```
upstreams ──► hookrelay ──► hookjudge ──► hookrelay ──► lark / dingtalk / wecom / webhook
              (adapts)      (judges)      (formats)
```

hookjudge has exactly one outbound address: the pipe. Fan-out is not its
business, so it cannot grow a second downstream by accident.

## The two shapes at the edges

Everything that crosses the boundary is in `hookjudge/contract.py`, in one
file, so a change to either shape is a change you can see.

**In** — the pipe's normalized event:

```json
{"meta":  {"source": "grafana", "correlation_id": "hr-2481"},
 "event": {"title": "...", "body": "...", "level": "high", "fields": {"env": "prod"}},
 "raw":   {"...": "the original, for analysis context"}}
```

hookrelay's `payload: normalized` channel sends the same information **flat**
— `{event_id, source, title, body, level, fields, received_at}` — and puts the
correlation id in an `X-Hook-Correlation-Id` header rather than the signed
body. Both shapes parse; a test pins the flat one to hookrelay's actual wire
bytes. That test exists because reading only the wrapped shape turned every
real delivery into empty strings, and the failure is worse than it sounds:
identity collapses to one constant for every alert, so the second event and
all after it reuse the first one's verdict forever — and the near-zero paid
ratio that produces looks like excellent cost savings.

**Out** — the judgement, posted back to a pipe door:

```json
{"meta":     {"brain": "hookjudge", "alert_name": "...", "source": "grafana",
              "importance": "high", "route": "ai", "is_recovery": false,
              "correlation_id": "hr-2481", "timestamp": 1754000000.0},
 "analysis": {"summary": "...", "event_type": "business",
              "impact_scope": "...", "importance": "high"},
 "identity": {"env": "prod"},
 "links":    []}
```

`identity` goes back as **data**, not a rendered string: choosing separators
and order is layout, and layout belongs to the pipe.

## Four routes, and the order is the cost policy

Every judged event has exactly one route, and it is the first question anyone
asks about the bill — what did we actually pay for?

| route      | when                                            | cost |
| ---------- | ----------------------------------------------- | ---- |
| `recovery` | the condition ENDED; reuse what its firing said  | 0    |
| `reuse`    | same identity judged inside the window           | 0    |
| `ai`       | a model read it                                  | paid |
| `rule`     | the model was unavailable, slow, or unusable     | 0    |

Two decisions worth stating out loud:

- **Recovery is read, never asked.** Whether an alert ended is a fact about the
  alert, not an opinion. Re-analysing a recovery costs a call *and* risks
  contradicting the original — a recovery that disagrees with its own firing
  alert reads as two unrelated events to whoever is on call.
- **A degraded verdict says it degraded.** When the model cannot answer,
  keyword rules produce a defensible floor and stamp `degraded_reason`. A
  downgraded judgement that hides its downgrade is worse than a missing one.

Only real `ai` verdicts are reusable. Reusing a `rule` verdict would spread one
degraded answer across a whole storm; reusing a `reuse` would let a single
judgement live forever by being re-served.

Identity — what makes two events the *same* condition — is the source, the
title, and the identity-ish fields, deliberately not the whole payload.
Timestamps and sequence numbers differ every time, and including them would
make every alert unique, which is the same as having no reuse at all.

## The ledger

One row per judged event, in SQLite: which route produced it, what it cost, how
long it took, and whether the result made it back. Reuse reads from that same
table, so the memory and the account are one thing rather than a cache sitting
beside a log.

The return leg is `queued → sent | dead`, retried on a backoff
(5s, 30s, 2m, 10m, 30m). A judgement nobody received is not a delivered
judgement, and the ledger does not pretend otherwise. Retention purging never
deletes a queued return.

## Endpoints

| method | path       | notes                                            |
| ------ | ---------- | ------------------------------------------------ |
| POST   | `/events`  | the pipe's event. Answers **202** immediately.    |
| GET    | `/status`  | ledger JSON: routes, cost, returns, recent        |
| GET    | `/metrics` | Prometheus text                                   |
| GET    | `/healthz` | liveness                                          |
| GET    | `/`        | a dark one-page view of the ledger                |

**Why 202 and not the verdict.** Judging takes tens of seconds. Holding the
sender's connection open for that makes it time out and retry, so the same
alert arrives twice while the first copy is still being analysed. The
judgement travels back the other way, to a pipe door, once it exists.

`/status` and `/metrics` are behind `HOOKJUDGE_READ_TOKEN`
(`X-Read-Token:` or `Authorization: Bearer`) — Prometheus can scrape with
either.

## Signatures

Both legs speak the pipe's scheme: HMAC-SHA256 over `{timestamp}.{body}`, with
a 300s freshness window so a captured delivery expires. Body-only is accepted
inbound when no timestamp header is present.

- inbound: `HOOKJUDGE_INGEST_SECRET` — empty means accept unsigned, which is a
  decision for a private network and never a default to drift into.
- outbound: `HOOKJUDGE_RETURN_SECRET` — must match the pipe door's secret.

## Configuration

All environment, one flat object (`hookjudge/settings.py`), no layers.

| variable | default | what it does |
| -------- | ------- | ------------ |
| `HOOKJUDGE_DB` | `hookjudge.db` | ledger path |
| `HOOKJUDGE_INGEST_SECRET` | *(empty)* | inbound HMAC secret |
| `HOOKJUDGE_READ_TOKEN` | *(empty)* | guards `/status` and `/metrics` |
| `HOOKJUDGE_MAX_BODY_BYTES` | `262144` | inbound body cap |
| `HOOKJUDGE_RETURN_URL` | *(empty)* | the one pipe door results go back to |
| `HOOKJUDGE_RETURN_SECRET` | *(empty)* | outbound HMAC secret |
| `HOOKJUDGE_RETURN_MAX_ATTEMPTS` | `6` | then the row is dead-lettered |
| `HOOKJUDGE_WORKER_INTERVAL` | `1.0` | return-leg tick, seconds |
| `HOOKJUDGE_REUSE_WINDOW_SECONDS` | `3600` | how long one verdict answers restatements |
| `HOOKJUDGE_RETENTION_DAYS` | `30` | `0` disables purging |
| `HOOKJUDGE_AI_BASE_URL` | *(empty)* | OpenAI-compatible base; empty = rules only |
| `HOOKJUDGE_AI_API_KEY` | *(empty)* | |
| `HOOKJUDGE_AI_MODEL` | `gpt-4o-mini` | |
| `HOOKJUDGE_AI_TIMEOUT_SECONDS` | `60.0` | |
| `HOOKJUDGE_AI_BODY_LIMIT` | `4000` | chars of body sent to the model |
| `HOOKJUDGE_AI_PRICE_IN_PER_1K` / `_OUT_PER_1K` | `0.0` | so the ledger can price tokens |

Leave the AI variables empty and the service still works: every event lands on
the rule floor and says `AI 未配置`.

The prompt in `hookjudge/judge.py` is Chinese on purpose. These alerts are
Chinese and the summaries are read by Chinese-speaking operators, so the model
must answer in the language of the room. That is a product decision, not
display copy.

## Running it

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env          # then fill in what you need
.venv/bin/python -m hookjudge # http://127.0.0.1:8200
```

or

```bash
docker compose up -d --build
```

## Wiring it behind hookrelay

Add a channel that hands the normalized event to the judge, and a door that
takes the judgement back:

```yaml
channels:
  - name: to-judge
    type: generic
    url: http://hookjudge:8200/events
    payload: normalized          # the judge wants the event, not a card
    secret: ${JUDGE_INGEST_SECRET}

sources:
  - name: judge-notify           # the return door
    secret: ${JUDGE_RETURN_SECRET}
    title: "{meta.alert_name}"
    body: "{analysis.summary}"
    level: "{meta.importance}"

routes:
  - name: judged-out
    source: judge-notify
    send_to: [lark, dingtalk]    # payload: processed on those channels
```

The pipe stamps `X-Hook-Correlation-Id: hr-<event_id>` on the way in;
hookjudge quotes it back in `meta.correlation_id`, so `GET /trace/{id}` on the
pipe assembles the whole round trip — origin, every brain, per-brain latency.

## Development

```bash
bash scripts/gate.sh   # the full gate — an exact replica of the CI job
```

Run it before every push. A local list that is merely "close enough" is how a
red CI arrives as a surprise; a test pins `scripts/gate.sh` and
`.github/workflows/ci.yml` to each other, so adding a check to one requires
adding it to the other in the same change.
