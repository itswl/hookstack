# Running the whole family

The pipe and the brain, plus a downstream you can read and a stub model, in
one command.

```bash
docker compose up -d --build
```

```
you ──► hookrelay :8100 ──► hookjudge :8200 ──► hookrelay ──► sink
        /hook/inbound            (judges)      /hook/judge-notify
        /hook/alertmanager
```

| where | what |
| --- | --- |
| http://127.0.0.1:8100/ | the pipe's ledger page |
| http://127.0.0.1:8200/ | the brain's ledger page (judged / paid ratio / cost / return failures) |
| `docker compose logs -f sink` | what an operator would actually have received |

The pipe's config is `examples/stack.yaml`, mounted read-only, so `up` needs no
setup step. Point `HOOKRELAY_CONFIG_FILE` at your own file to replace it.

**A stub model is wired in by default.** Without one, every event lands on the
`rule` floor and `reuse` — which only ever follows an `ai` verdict — never
happens, hiding half the cost policy. The stub is not a model: it returns
canned verdicts and reports itself as `stub-4o-mini`. Put real credentials in
`.env` and it stops being used.

Production does **not** use this file. `deploy/docker-compose.prod.yml` runs
the pipe alone, bound to loopback, on WebhookWise's docker network.

## Drive the whole cost policy

```bash
fire() { curl -s -X POST "http://127.0.0.1:8100/hook/$1" \
  -H 'content-type: application/json' -d "$2"; echo; }

# 1 firing — pays
fire inbound '{"title":"支付网关 5xx 比例 8.1%","message":"gateway-2 近 5 分钟 5xx 8.1%","state":"alerting","env":"prod"}'

# 2 the same condition restated — reuses, costs nothing
fire inbound '{"title":"支付网关 5xx 比例 8.1%","message":"gateway-2 近 5 分钟 5xx 8.4%","state":"alerting","env":"prod"}'

# 3 a different condition, through the Alertmanager door — pays
fire alertmanager '{"status":"firing","commonLabels":{"alertname":"DiskWillFill","env":"prod"},
  "alerts":[{"status":"firing","labels":{"alertname":"DiskWillFill","env":"prod","service":"k8s"},
  "annotations":{"summary":"k8s 节点磁盘使用率 93%","description":"node-3 /var 剩余 7%"}}]}'

# 4 it recovers — inherits what its firing was judged to be, costs nothing
fire alertmanager '{"status":"resolved","commonLabels":{"alertname":"DiskWillFill","env":"prod"},
  "alerts":[{"status":"resolved","labels":{"alertname":"DiskWillFill","env":"prod","service":"k8s"},
  "annotations":{"summary":"k8s 节点磁盘使用率 93%","description":"已回落至 41%"}}]}'
```

`curl -s http://127.0.0.1:8200/status` then shows:

```
judged 4   paid ratio 50.0%   cost $0.000524   returns {'sent': 4}
routes {'ai': 2, 'reuse': 1, 'recovery': 1}

#1 firing    ai        critical  $0.000262  支付网关 5xx 比例 8.1%
#2 firing    reuse     critical  $0         支付网关 5xx 比例 8.1%   <- summary identical to #1
#3 firing    ai        high      $0.000262  k8s 节点磁盘使用率 93%
#4 RECOVERY  recovery  high      $0         k8s 节点磁盘使用率 93%   <- inherits #3's high
```

One judgement reaches every downstream in its own dialect — the sink logs show
the same verdict as a Feishu card and as DingTalk markdown.

## Three things worth watching

Whether this is working correctly comes down to these, and each has genuinely
been broken:

1. **#4's importance must equal #3's.** If it does not, the recovery never
   linked to its firing and the `recovery` route did not run — on call you
   would see an alert at `high` and its recovery at `medium`.
2. **Step 4's text contains no recovery word** (the description reads
   "已回落至 41%"). Alertmanager signals recovery with `status: resolved`, and
   `level_map` turns that into `info` — so `examples/stack.yaml` carries
   `status` as a field, or the brain sees no sign of recovery at all.
3. **Two different alerts must have two different identities** (visible on
   `/status`). If they all share one, the brain is parsing a shape the pipe is
   not sending — and the near-zero paid ratio that produces looks like
   excellent cost savings.

## Upstream conventions

Two that are easy to get wrong, both encoded in `examples/stack.yaml`:

**Array paths use a dotted index.** `alerts.0.annotations.summary` resolves;
`alerts[0].annotations.summary` returns nothing and does it silently, so the
title just comes out empty.

**Carry the upstream's state as `fields.status`.** See point 2 above. The brain
excludes state fields from identity (alongside timestamps), so carrying it does
not split the firing/resolved pair into two conditions.

## Down

```bash
docker compose down          # keep the ledgers
docker compose down -v       # delete them too
```

## Known gaps

- **`reuse` saves money, not attention.** Step 2 reused the verdict and paid
  nothing, but the downstream still got an identical card. This posture turns
  the pipe's dedup OFF on the grounds that the brain owns noise accounting, and
  the brain currently accounts only for spend. Two ways to close it, and the
  choice is a product decision: the brain returns a suppression signal on
  `reuse` and the pipe drops the delivery (one place decides what is noise, but
  a suppressed card can hide a genuine escalation), or the pipe deduplicates
  the return door (simpler, but two components decide what is noise and the
  brain's ledger stops describing what was delivered).
- **AWS SNS cannot be templated.** Its real payload is a JSON *string* inside
  `Message`, and extraction paths cannot reach into a string, so
  `Message.AlarmName` resolves to nothing. It needs a source adapter, and a
  real one also has to handle the SubscriptionConfirmation handshake and
  certificate-based signatures.
