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
