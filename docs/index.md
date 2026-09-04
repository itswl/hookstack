---
title: hookstack
description: Run agents in production and account for them afterwards — a signed, priced, replayable bus for agent handovers, plus a read-only agent runner you can use entirely on its own.
---

**English** · [中文](zh/)

Run agents in production and be able to account for them afterwards.

The shape is always the same: **something produces signals, a pipe carries them
and accounts for every hop, nodes decide or investigate, and what survives
reaches a person.** Three services fill it — a content-blind pipe, a judge, and
a read-only investigator — wired by a config file rather than by code. Each
handover is signed, retried, dead-lettered, priced and replayable, and each
agent runs behind a credential, a budget and a closed list of what it may do.

**Alerts are the instance this was worn in on**, and most examples below speak
that dialect. They are not the shape — the second deployment in this repository
carries an operator's own work signals, chat and tickets, through a watcher and
a planner, on the same code.

The agent runner ([hookprobe](#hookprobe-an-agent-run-behind-an-http-contract))
is useful entirely on its own, whether or not you care about alerts.

MIT licensed. `docker compose up`. → **[github.com/itswl/hookstack](https://github.com/itswl/hookstack)**

---

## hookprobe: an agent run behind an HTTP contract

You POST a task. hookprobe runs a single tool-using agent session (Claude Agent
SDK: bash, MCP servers, web search, `SKILL.md` skills) and serves the report to
whoever polls for it. No channels, no device pairing, no chat history.

It speaks an OpenClaw-compatible dialect, so a client that already treats that
gateway as its analysis backend switches by changing a URL.

```bash
git clone https://github.com/itswl/hookstack && cd hookstack
printf 'HOOKPROBE_TOKEN=change-me\nANTHROPIC_API_KEY=sk-ant-...\n' > .env
docker compose --env-file .env -f hookprobe/deploy/docker-compose.yml up -d --build

curl -s -X POST localhost:8088/hooks/agent \
  -H "Authorization: Bearer change-me" -H 'Content-Type: application/json' \
  -d '{"message":"Which processes are listening, and on what?","sessionKey":"demo:1"}'

curl -s -H "Authorization: Bearer change-me" localhost:8088/sessions/demo:1/final
```

### Three things that make it different

**It terminates.** `/final` answers `202`, or a `200` that is final — including
when the run crashed or timed out, where the report's `root_cause` names the
runner failure. A poller writes the result on its first confirming read and
never invents stability heuristics.

**The agent cannot edit what steers the next run.** `.claude/` (skills, roles,
settings), `CLAUDE.md` and the audit log are closed to it — by a PreToolUse hook
that refuses the write, and by a digest of every input file compared before and
after each run, because the two fail differently. Without this, one injected
line reaching `.claude/skills/` outlives the run that read it and comes back as
the operator's own runbook.

**Finished runs leave runbooks behind.** A completed investigation distills its
own record into a `SKILL.md`, written by the service and never through the
agent's tools. The second investigation of the same condition adds a case rather
than replacing what was there, and every write — by a run or by a person —
snapshots what it displaced first.

![The sessions console](img/hookprobe-sessions.png)

![Every tool call of an investigation, as it happens](img/hookprobe-live-feed.png)

![The diagnostic runbook a run distilled for itself](img/hookprobe-skills.png)

---

## Running an agent where it can cost you something

An agent here is an untrusted network service that spends money, reads text an
attacker may have written, and holds credentials. Everything below exists
because one of those three is true.

| | |
| --- | --- |
| **Signed handovers** | every door verifies a timestamped HMAC; every node has its own secret, budget and guards |
| **A closed list of what it may do** | `HOOKPROBE_MCP_TOOLS` names the MCP tools an instance may call, and **empty denies all of them**. Mounting a server does not grant its tools — no server is read-only just because you wanted it to be, and a chat server ships `send_message` beside `search_messages` |
| **A closed vocabulary for what it may conclude** | a verdict that can steer a route is picked from a set the operator declared, never written free-hand into one |
| **Read-only by construction** | mutating verbs refused before they run (`aws` is refused unless the command *reads*), read-only credentials as the real boundary, and a hook that stops a run editing what steers the next one |
| **A ceiling on spend** | past the budget, new autonomous runs are refused — and each refusal reports itself instead of going quiet |
| **The graph before you change it** | `GET /topology` renders doors, stages and exits from config alone, and names the hazards the shape implies: a door nothing can reach, an exit nothing feeds, a return door that can fall through to a wildcard and hand a brain its own output |
| **The journey after** | `GET /trace/{id}` replays both directions of every hop — bodies only, never headers, because headers carry signatures and tokens |

The honest version of all of it, including what each boundary does **not** stop,
is [docs/containment.md](https://github.com/itswl/hookstack/blob/main/docs/containment.md).
A guard that is only described by what it catches gets trusted for things it
never claimed.

### Not everything through the pipe is an alert

The judge earns its keep on alerts — severity, recovery, flapping, the four
routes ordered by cost. A source that has ALREADY decided (a watcher forwarding
"this needs a person", a system that only emits what matters) gains nothing from
being judged again, and would be judged in a vocabulary that does not fit it.

So a route may be terminal: a signed door of your own, straight to the channel
you named, past the judge. It still gets the pipe's ledger, retries and dead
letters — the parts that are about delivery rather than about content.

The investigator is still reachable from there, and asks a different question
when the event is work rather than a fault: what exists now, what is missing,
the steps, how the result would be verified — and what it could not see, named
rather than guessed at. It proposes nothing for execution either way.

### One codebase, two very different graphs

The repository runs two deployments that share every line of service code. One
carries alerts from a monitoring platform through three judges to a card. The
other carries an operator's own work signals — chat and tickets — through a
watcher to two different chats, with a planner on the branch that is actually
work. Neither needed a line of Python the other did not, and the four places
they diverge each have a reason:
[docs/deployments.md](https://github.com/itswl/hookstack/blob/main/docs/deployments.md).

---

## The whole family

| Component | Role | Deliberately does NOT |
| --- | --- | --- |
| **hookrelay** | the pipe — adapts every upstream dialect in and every channel format out, and accounts for all of it | understand content, or judge |
| **hookjudge** | the judge — one event in, one verdict out, four routes ordered by cost | render cards, or know channels |
| **hookprobe** | the investigator — one read-only agent run per event that earns it, answering *what broke* for an alert and *how would this be done* for a work item | receive alerts, or send notifications |

```
upstream alert sources (Grafana / Alertmanager / cloud monitoring …)
      │
      ▼
  hookrelay :8100 ──► hookjudge :8200 ──► hookrelay ──► Feishu / DingTalk / WeCom
  (pipe: adapt+route+ledger) │ (judge: verdict+cost)   (formats and delivers)
                             │
                             └──► hookprobe :8088 ──► hookrelay ──► the same channels
                                  (investigator: read-only)  /hook/probe-notify

a source that already judged its own signal takes a terminal route instead:

  your source ──► hookrelay ──┬──► the channel you named   (no judge: it is decided)
  (signed door)               └──► hookprobe :8088         (only if it says so)
```

![hookrelay's ledger: every message accounted for, every delivery with an outcome](img/hookrelay-ledger.png)

![hookjudge's status page: four verdicts with their routes](img/hookjudge-status.png)

Every screenshot above comes from one local Docker run started from nothing —
not mockups.

---

## Read more

- [Full narrative overview](https://github.com/itswl/hookstack/blob/main/OVERVIEW.md)
- [hookprobe reference](https://github.com/itswl/hookstack/blob/main/hookprobe/README.md)
- [Running the whole family](https://github.com/itswl/hookstack/blob/main/STACK.md)
- [WebhookWise](https://github.com/itswl/WebhookWise) — the self-hosted alerting platform these grew out of
