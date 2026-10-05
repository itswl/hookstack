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

![hookprobe's console: a real investigation, conclusion first, the root cause it could not establish said so, and the bill under it](img/hookprobe-sessions.png)

## Try it: ten minutes, no keys, no bill

```bash
curl -fsSLO https://raw.githubusercontent.com/itswl/hookstack/main/docker-compose.quickstart.yml
docker compose -f docker-compose.quickstart.yml up -d   # pipe + judge + stub model + investigator on a rehearsal + readable sink
bash <(curl -fsSL https://raw.githubusercontent.com/itswl/hookstack/main/scripts/demo.sh)
```

The demo runs the whole loop. An alert is judged, a recorded investigation replays through the real read-only gate, the report arrives as a card, an approve press runs two allowlisted commands, and the alert resolves. To use a real model, put `HOOKPROBE_RUNTIME=claude`, `HOOKPROBE_MODEL` and a key in `.env`.

## What you get

- **Only the work worth doing.** A storm, a restatement or a recovery never buys a second verdict. On 795 production alerts, 28 of 29 rules answered the same every time, so `rule-reuse` answers them for free.
- **A card a person can rule on.** Every button is signed before the card leaves, and every press is recorded.
- **Remediation with a person in the loop.** An approved procedure runs step by step against an allowlist, as argv, never through a shell. A plan a person hands off is carried out by a separate work node, with a write credential of its own and a guard that refuses destructive commands; it made its first real change on 2026-09-30 ([how it went](deployments.md#the-first-real-write)).
- **Investigations that leave something behind.** A finished run distills a runbook, and the next occurrence starts from it.
- **Agents you can contain.** Read-only by default and measured at startup, budget ceilings that refuse out loud, and twenty-nine structural boundaries, each written up with what it does **not** stop ([containment](containment.md)).
- **One audit page per event.** Every hop, digest, decision and human action, a flight recorder of every tool call, and a timing waterfall per run.
- **Your model, your chat.** Any Anthropic-dialect endpoint for the investigator, any OpenAI-compatible one for the judge, local models included. Feishu/Lark through a bridge ([protocol](bridge-protocol.md)), DingTalk and WeCom as a plugin, or signed JSON to any webhook.

![hookrelay's board: one sentence on what waits on you, four numbers, and one row per alert with its seven stages](img/hookrelay-timeline.png)

![hookjudge's board: verdicts with their routes and what each cost](img/hookjudge-status.png)

![hookrelay's ledger: every event with its decision chain, its deliveries, and what came back](img/hookrelay-ledger.png)

The three boards read in Chinese or English and follow the system's light or dark. The screenshots come from local runs, not mockups.

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
*   [WebhookWise](https://github.com/itswl/WebhookWise) — the self-hosted alerting platform these grew out of

MIT licensed.
