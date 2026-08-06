# hookjudge behind hookrelay

The paired topology, as verified end to end. One alert produces:

```
POST /hook/inbound        →  relay event, routed to `to-judge`
POST /events              →  202, judged in the background
POST /hook/judge-notify   →  the judgement, signed, correlated
                          →  a Feishu card built by the pipe
```

## What a verified run looks like

Firing, then the same condition restated, then recovery:

| # | state | route | importance | card |
| - | ----- | ----- | ---------- | ---- |
| 1 | firing | `rule` | high | 📡 red |
| 2 | firing (restated) | `rule` | high | 📡 red |
| 3 | recovered | `recovery` | high | ✅ green |

Two things to check, because both were broken once and both looked fine from
a distance:

- **The recovery's importance equals its firing's.** If the recovery card is a
  different severity than the alert it closes, identity normalization is not
  linking the pair and the `recovery` route never ran.
- **Two different alerts have two different identities** on `/status`. If every
  event shows the same identity, the brain is parsing an envelope shape the
  pipe is not sending — and the resulting near-zero paid ratio will look like
  excellent cost savings rather than a broken parser.

## Note on restatements

Row 2 was judged independently rather than reusing row 1, which is correct:
only real `ai` verdicts are reusable, and this run had no model configured.
With `HOOKJUDGE_AI_*` set, row 2 becomes `reuse` at zero cost.

## Secrets

Every `${...}` resolves from the environment. Put them in a `.env` beside this
file. All are optional for a local trial — empty secrets mean unsigned doors,
which is a decision for a private network and never a default to drift into.

## Upstream ecosystems

The pipe adapts each one; the brain only ever sees the normalized event. Two
conventions matter, both learned the hard way:

**Array paths use a dotted index.** `alerts.0.annotations.summary` resolves;
`alerts[0].annotations.summary` returns nothing and does it silently, so the
title just comes out empty.

**Carry the upstream's state as `fields.status`.** Alertmanager signals recovery
with `status: resolved`, which `level_map` turns into level `info` — and its
resolved body reads "已回落至 0.2%", which contains no recovery word. So without
that field there is no recovery marker anywhere the brain looks, and a resolve
is judged as a brand-new alert. The brain excludes state fields from identity
(alongside timestamps), so carrying it does not split the firing/resolved pair
into two conditions.

### Known gap: AWS SNS

SNS is not templatable. The real payload arrives as a **JSON string** in
`Message`, and extraction paths cannot reach inside a string —
`Message.AlarmName` resolves to nothing. It needs a source adapter rather than
a template, and a real one also has to handle the `SubscriptionConfirmation`
handshake and SNS's certificate-based signatures. Not built.
