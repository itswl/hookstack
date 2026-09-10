---
title: A reply under a card that was never an investigation is a question, not a follow-up to nothing
status: implemented
date: 2026-09-10
scope: hookrelay
---

## Decision

When `thread_lookup` resolves a reply to a chain that carries **no session**, it
shapes the message as a fresh question — the same `on_new_topic` knob a new
topic uses — instead of leaving it as `kind: follow_up`. It also writes
`fields.about`: what the card was about.

`deploy/work.yaml`'s route matches on `kind: task` rather than `topic: new`, so
both shapes reach the planner. Route renamed `topic-to-plan` → `question-to-plan`.

## Why

A live failure, and the operator asked about it directly: on 2026-09-10 09:40 the
watcher posted a notification; at 09:42 a person replied under it, @-mentioning
the bot — *"看一下这个 ip 属于啥服务"* — and nothing happened.

The trace says exactly where it stopped:

```
thread-lookup  resolved   origin_event_id 243   session null
routes         topic-to-plan  missed on topic
               thread-to-plan missed on return_source
               thread-to-work missed on return_source
outcome: skipped   skip_code: no_route
```

The card was a watcher's notification, so its chain has no investigation in it.
The stage resolved it (it IS a card this pipe sent), wrote `session: null`, and
defaulted `kind` to `follow_up`. Every thread route wants either `topic: new` or
a `return_source` — a notification has neither.

**`follow_up` was the wrong shape, not the routes.** A follow-up continues a
session; there is no session. Even on the shadow config, whose thread route is a
catch-all with no `when`, the message would have reached the investigator and
been answered *"no investigation behind this thread"* — an answer that reaches a
ledger, not the person who asked. Failing one step later is still failing
silently.

**`fields.about` is what makes the answer possible at all.** A person replying
under a card does not repeat it: the message names no IP. Without the card's
title the question arrives with its subject missing, and the model would have
had to answer *"which ip?"* — which is the same silence with extra steps.
`thread_context` already returned `title`; nothing had ever read it.

**One knob, two triggers.** A new topic and a reply-under-a-notification are, from
the deployment's side, the same thing — somebody asked something and there is no
session behind it — and the shaping answer is the same. A deployment that has not
set `on_new_topic` keeps the old behaviour exactly, so an upgrade does not
acquire a new paid door.

Verified by replaying the operator's own message through the door: `routed`,
stage `asked`, `question-to-plan` matched, planner ran, `return: sent`, and the
bridge logged `card delivered (in thread)`. The answer identified the IP as a
load balancer's elastic IP — which it could only do from `fields.about`.

## Consequences

* **A reply under any card this pipe sent can now start a paid run** on a
  deployment that sets `on_new_topic`. Gated as before: the sender must be in
  `HOOKPROBE_FOLLOW_UP_SENDERS`, the budget breaker applies, and the person has
  to @-mention the bot for the reply to reach the bridge at all. The replay cost
  $2.24.
* The step result is `asked` rather than `resolved`, so the two are
  distinguishable in `/trace` afterwards.
* `deploy/shadow.yaml` needs no route change — its thread route has no `when` —
  but it does need the code change to stop shaping these as follow-ups.
* Still unfixed, and now the only silent path left here: a reply that resolves to
  nothing at all (`unknown_thread`), and a `no_route` on any source. Those answer
  the pipe, and the pipe answers its ledger.
