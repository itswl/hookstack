---
title: hookstack
description: Three services that split alert handling — plus a read-only agent runner you can use entirely on its own.
---

**English** · [中文](zh/)

Alert handling, split into three services that each do one job — and an agent
runner ([hookprobe](#hookprobe-an-agent-run-behind-an-http-contract)) that is
useful on its own, whether or not you care about alerts.

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

## The whole family

| Component | Role | Deliberately does NOT |
| --- | --- | --- |
| **hookrelay** | the pipe — adapts every upstream dialect in and every channel format out, and accounts for all of it | understand content, or judge |
| **hookjudge** | the judge — one event in, one verdict out, four routes ordered by cost | render cards, or know channels |
| **hookprobe** | the investigator — one read-only agent run per alert that earns it | receive alerts, or send notifications |

```
upstream alert sources (Grafana / Alertmanager / cloud monitoring …)
      │
      ▼
  hookrelay :8100 ──► hookjudge :8200 ──► hookrelay ──► Feishu / DingTalk / WeCom
  (pipe: adapt+route+ledger) │ (judge: verdict+cost)   (formats and delivers)
                             │
                             └──► hookprobe :8088 ──► hookrelay ──► the same channels
                                  (investigator: read-only)  /hook/probe-notify
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
