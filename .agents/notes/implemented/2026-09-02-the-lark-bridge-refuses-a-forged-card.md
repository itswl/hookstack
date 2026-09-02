---
title: The lark-bridge can require the pipe's signature on an inbound card
status: implemented
date: 2026-09-02
scope: stack
---

## Decision

`deploy/lark-bridge/bridge.py` gains `is_authentic()`: when
`BRIDGE_INBOUND_SECRET` is set, an inbound card POST must carry a fresh
Feishu-style signature (`{timestamp, sign}`, `sign =
base64(HMAC-SHA256("{ts}\n{secret}", ""))`) or it is refused with 401. Unset =
accept everything, exactly as before.

The other half is config: `deploy/shadow.yaml`'s `to-me` channel secret is now
`${LARK_BRIDGE_SECRET}` (the feishu builder adds the sign when a channel has a
secret), and the compose passes `LARK_BRIDGE_SECRET` to hookrelay and the same
value as `BRIDGE_INBOUND_SECRET` to the bridge. All default empty, so the
feature is off until one `.env` line sets it on both sides.

## Why

The bridge listens on `0.0.0.0:9100` over `hookstack_net`, which is SHARED with
the platform's containers — so "only the pipe can reach it" was never true, and
anything on that wire could POST a card straight into the operator's private
chat as the application (phishing/annoyance, not data theft, but on the one
surface a person trusts). The existing feishu signing gives a shared-secret
proof for free; reusing it means no new signing code and no new wire format.

The sign covers the timestamp, not the card, so a captured card can be replayed
within the 300s skew — bounded, and the card is not attacker-chosen. Forging a
NEW card needs the secret. Fail-open-when-unset makes turning it on a config
change with no delivery risk from the code itself: an unset bridge never rejects.

Verified before shipping: the bridge's check accepts hookrelay's exact signer
output, rejects a wrong secret, a missing sign, and a stale one, and accepts
anything when the secret is unset.

## Consequences

- Turning it on is a `.env` line (`LARK_BRIDGE_SECRET`) plus a redeploy; a
  mismatch would 401 and hookrelay would dead-letter the card in the open (a
  visible failure, not a silent drop). Verify a real card still delivers right
  after enabling.
- Still open on the bridge (proposed note): secret ROTATION is ignored by
  `entrypoint.sh` (keeps an existing config), and the bridge still runs as root.
