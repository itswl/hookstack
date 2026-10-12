---
title: hookstack
description: Put agents on your production signals without handing them the keys — a pipe that signs, routes and prices every signal, an investigator that runs read-only and proves it at startup, and an audited result that reaches a person. Self-hosted; the investigator also runs entirely on its own.
---

**English** · [中文](zh/)

**Put agents on your production signals without handing them the keys.**

A signal comes in: an alert, a chat message, a ticket, a timer. A pipe signs, routes and prices it. An agent investigates it read-only, and proves it is read-only at startup. The report reaches a person as a card they can rule on. Nothing touches production without a signed press.

| service | what it does |
| --- | --- |
| `hookrelay` | **The pipe.** Adapts any webhook, routes it, delivers to chat, and keeps one ledger of every hop. |
| `hookjudge` | **The judge.** Decides cheaply whether a person must act now. Most events never pay for a model. |
| `hookprobe` | **The investigator.** One read-only agent run per event, behind an OpenClaw-compatible HTTP contract. It also runs on its own. |

The investigator is the product. The pipe and the judge are the smallest alerting front end for a team that has none. One deployment is one team: this is not a work-management platform, a multi-tenant SaaS or an agent framework.

## Start here

- **30 seconds:** [docs/quickstart.md](quickstart.md) opens with the shape of it — three services, one diagram.
- **10 minutes, no keys, no bill:** the same page's second section runs the whole loop on a rehearsal: an alert judged, an investigation replayed through the real read-only gate, a card, an approve press, a resolved alert.
- **Half a day, for real:** [STACK.md](https://github.com/itswl/hookstack/blob/main/STACK.md) runs all three from source; [docs/containment.md](containment.md) is the boundary map you read *before* pointing it at a cluster.
- **What it does, in full:** [docs/what-you-get.md](what-you-get.md).

![hookprobe's console: a real investigation, conclusion first, the root cause it could not establish said so, and the bill under it](img/hookprobe-sessions.png)

The three boards read in Chinese or English and follow the system's light or dark; [the full tour](https://github.com/itswl/hookstack/blob/main/OVERVIEW.md) has every screenshot, from local runs and not mockups.

## How it fits together

```
upstreams ──► hookrelay ──► hookjudge ──► hookrelay ──► chat bridge / webhook
                  │
                  └──► hookprobe ──► hookrelay ──► the same channels
                       (critical/high; a rehearsal until it has a model)
```

The same pipe also carries an operator's own work signals to a planner and, on a person's press, to a work runner ([two deployment shapes, one codebase](deployments.md)).

## Read more

*   [Full narrative overview](https://github.com/itswl/hookstack/blob/main/OVERVIEW.md)
*   [hookprobe reference](https://github.com/itswl/hookstack/blob/main/hookprobe/README.md)
*   [Running all of hookstack](https://github.com/itswl/hookstack/blob/main/STACK.md)
*   [Containment](containment.md) — the security boundaries

MIT licensed.
