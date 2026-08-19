---
title: A card carries buttons; the brain declares them and the pipe signs them
status: implemented
date: 2026-08-19
scope: stack
---

## Decision

A notification card can carry actions, and pressing one does something. Five
kinds exist: `silence`, `followup`, `approve`, `useful`, `useless`.

The split is one notch finer than the old contract described:

    the brain declares WHICH actions its result deserves   — judgement
    the pipe mints, carries and verifies the token         — the channel edge

hookjudge returns `{"kind": "silence", "text": "Silence 15m", "minutes": 15}`
and hookprobe returns one `approve` per proposal its investigation produced.
Neither holds a signing secret and neither names a channel. hookrelay turns a
declaration into an opaque signed token, renders it as a button, and is the only
component that can read one back — `POST /card-action`. `silence` it performs
itself; everything else becomes an event plus a delivery to a configured
channel, so a forwarded press inherits the retry, the rate limit, the dead
letter and the ledger row instead of growing a second delivery mechanism.
`hookprobe` receives those at `POST /hooks/action`, `hookjudge` at
`POST /feedback`.

Which kinds a deployment offers is config (`card_actions` in the pipe's YAML),
and an unlisted kind is dropped before minting. `approve` runs commands, so it
is opt-in per deployment; a brain asking for it is making a request, not a
guarantee.

## Why

The product's whole promise is that a human gets interrupted well. But every
useful response to being interrupted — quiet this, ask what it meant, approve
the fix it proposed — lived behind a web board on a different port with its own
bearer token. At 3am nobody opens the board. So the report was read and that was
the end of it, and the capabilities behind those endpoints went unused: the
`actions` slot had been in the processed-event contract since it was written,
rendered by the Feishu builder, and **no brain had ever populated it and no door
existed to receive a press**.

Two things were decided rather than assumed:

**Who signs.** The contract's original comment said actions arrive "pre-signed
by the brain, because a signature is judgement about identity, not formatting."
That reasoning is sound and was still rejected, because signing needs a secret:
three brains signing means the secret lives in three places and the same HMAC
comparison gets written three times. That is not hypothetical here — it is
exactly how one non-ASCII byte became an unauthenticated HTTP 500 in five files
at once, found the same day (see
`2026-08-19-the-three-copies-of-the-wire-and-the-board`). Declaration is
judgement and stays with the brain; authentication is the channel edge's job and
now exists once.

**Claim before acting.** The press is claimed in the ledger — `card_actions.jti`
is UNIQUE — *before* anything happens, and a second press answers
`already_done`. The actions on the far side of this door spend money (a
follow-up turn is a paid model call) and restart services, so a double press has
to lose a race rather than be handled twice. A card forwarded into a group chat
is a token sitting in everyone's scrollback, which is also why tokens carry a
TTL (24h by default) and why an empty `HOOKRELAY_ACTION_SECRET` renders no
buttons at all rather than unsigned ones.

## Consequences

- `/trace/{id}` now answers "and what did a person do about it", because the
  presses are ledger rows next to the deliveries. The machine half of that
  timeline was always there; a morning review opens with the other half.
- hookjudge can finally account for **attention** and not only spend:
  `interruptions` (it always knew this number — the headline count was the
  interruption count, read as throughput), `repeats`, and how many a human ruled
  worth waking for. No suppression behaviour changed; that decision
  (`2026-08-12-who-owns-noise-when-a-verdict-is-reused`) stays open, and now has
  data to be decided against.
- hookprobe can answer "was the investigation worth its bill", which is the
  first objection anyone raises about paying a model per alert.
- A forwarded action's channel secret must equal the receiving door's secret,
  the same coupling the existing `to-judge` and `to-probe` channels already
  have.
- Both doors answer a card whose subject no longer exists with **202 and a
  reason**, and the dividing line is now written down: what an *operator* must
  fix stays non-2xx and earns the self-alarm (401 the secrets disagree, 400 the
  shape is wrong, 404 an id that never existed); the world having moved on is a
  202. The deciding case is retention — a card in a chat outlives its run, since
  `HOOKPROBE_RETENTION_DAYS` prunes case files, so scrolling up and pressing a
  stale button is the expected steady state. The pipe reads a non-2xx as a
  delivery failure, so a 404 there would retry with backoff, dead-letter and fire
  the one alarm that must not cry wolf — for a miss that is permanent anyway.

## Rejected

- **The brain signs its own actions**, per the original contract comment. See
  above: it puts a secret and an HMAC comparison in three services.
- **The pipe decides which actions to offer.** It is content-blind; choosing
  that a verdict deserves a silence button is a judgement about the verdict.
- **A dedicated action-delivery path.** Forwarding through the outbox was
  strictly less code and inherited retry, rate limiting and the ledger for free.
- **Verifying each IM platform's own callback signature as the primary control.**
  Every platform has a different scheme, and pinning one per platform is how
  this grows four verifiers. The token is the control that always applies;
  `HOOKRELAY_CARD_CALLBACK_SECRET` adds the family signature as a second layer
  for anyone who can put a gateway in front.
