---
title: Verify a chat app against its chat before cutting over — they can be in different tenants
status: implemented
date: 2026-09-09
scope: stack
---

## Decision

Switching the bridge's chat application is now a four-step operation with a
verification in front of it, not a two-line `.env` edit. The verification is the
point: an application and a chat can belong to **different tenants**, and no
amount of adding the bot to the group fixes that.

## Why

A production app switch was asked for as credentials only — an id and a secret.
Checking them before applying turned up something the request did not mention
and could not have: the new application could not reach the chat the deployment
was posting to. Not "was not a member of". Refused outright:

```
GET /im/v1/chats/<the chat production used>
  → 232010  Operator and chat can NOT be in different tenants
GET /im/v1/chats/<the new app's own group>
  → 0  success
```

A tenant boundary is an organisational one. The new app had exactly one chat, in
its own tenant, so honouring the request **forced** a second change nobody had
asked for: production alerts moving to a different organisation's group. That is
not a decision to infer from an id and a secret, and it was put to the operator
before anything was touched.

**The failure this avoids is silent.** Change the two credentials, recreate, and
every container is healthy, the bridge logs `bridge up`, both event streams
connect — and the first real alert fails to deliver, at whatever hour it
arrives. The bridge cannot tell "this chat does not exist for me" from any other
send failure until it tries to send.

## What to do instead

Four API calls, no writes except the last, before touching any config:

1. `POST /auth/v3/tenant_access_token/internal` — the credentials work at all.
2. `GET /application/v6/applications/{app_id}` — this is the app you think it
   is. It returns the display name; check it against what was asked for.
3. `GET /im/v1/chats/{chat_id}` for the chat the deployment **currently** uses.
   Success means the switch is credentials-only. `232010` means the chat must
   move too, and that is the operator's call.
4. One real card to the destination. Sending is the only way to verify the send
   scope: the new app here was missing `im:chat` member-read permissions
   entirely, which is what surfaced the tenant problem, and read scopes and send
   scopes are granted separately.

Then, and only then: back up `.env`, edit, recreate, and read it back through
the whole chain rather than the container's status.

## The mechanics, since they were also wrong in the docs

`docs/deployments.md` said switching apps meant clearing the CLI config on the
bridge's volume. Both halves were stale — the bridge has had no volume since
`20f432d`, so a fresh container writes `/root/.lark-cli/config.json` itself.
Corrected in `87f8188` by another session, using this switch as the evidence.

What actually holds:

- **Force-recreate, never `restart`.** A bare restart does not re-read `.env`.
- **hookrelay does not need recreating.** It reads `LARK_BRIDGE_SECRET`, never
  the app id or secret.
- **`lark-bridge-watch` is webhook mode** and has no application at all.
- **Read it back through the pipe**, not the container: a signed POST to
  `/hook/probe-notify` should answer `routed channels=[to-me]` and the bridge
  should log `card delivered`. Containers being healthy proves nothing here.

## Consequences

**A new app is a new blast radius.** The one switched to was missing read scopes
and lives in another tenant, which together say it was created for a different
purpose than the one it was handed to. Neither fact is visible from an id and a
secret, and both are one API call away.

**The old values are recoverable.** `.env.bak-<timestamp>` is written before the
edit and holds the previous app, secret and chat. Rolling back is that file plus
a force-recreate.
