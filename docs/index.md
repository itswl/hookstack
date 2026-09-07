---
title: hookstack
description: Run agents in production and account for them afterwards — a signed, priced, replayable bus for agent handovers, plus an agent runner, read-only by default, that you can use entirely on its own.
---

**English** · [中文](zh/)

Run agents in production with absolute financial accountability and structural containment.

The architecture is content-blind and decoupled: **something produces signals, a pipe carries them and accounts for every hop, specialized nodes decide or investigate, and what survives reaches a person.** Three services fill it — a content-blind pipe, a judge, and a read-only investigator — wired entirely by configuration rather than by code.

Every handover is cryptographically signed, retried, backoff-scheduled, and replayable. Every agent runs with restricted credentials, budget ceilings, and a closed list of permitted actions.

**Alerts are the instance this was worn in on**, and most examples below speak that dialect. They are not the shape — the second deployment in this repository carries an operator's own work signals, chat and tickets, through a watcher and a planner, on the same code.

The agent runner ([hookprobe](#hookprobe-an-agent-run-behind-an-http-contract)) is useful entirely on its own, whether or not you care about alerts.

MIT licensed. `docker compose up`. → **[github.com/itswl/hookstack](https://github.com/itswl/hookstack)**

---

## hookprobe: an agent run behind an HTTP contract

You POST a task. hookprobe runs a single tool-using agent session (Claude Agent SDK: bash, MCP servers, web search, `SKILL.md` skills) and serves the report to whoever polls for it. No channels, no device pairing, no chat history.

It speaks an OpenClaw-compatible dialect, so a client that already treats that gateway as its analysis backend switches by changing a URL.

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

**It terminates.** `/final` answers `202`, or a `200` that is final — including when the run crashed or timed out, where the report's `root_cause` names the runner failure. A poller writes the result on its first confirming read and never invents stability heuristics.

**The agent cannot edit what steers the next run.** `.claude/` (skills, roles, settings), `CLAUDE.md` and the audit log are closed to it — by a PreToolUse hook that refuses the write, and by a digest of every input file compared before and after each run, because the two fail differently. Without this, one injected line reaching `.claude/skills/` outlives the run that read it and comes back as the operator's own runbook.

**Finished runs leave runbooks behind.** A completed investigation distills its own record into a `SKILL.md`, written by the service and never through the agent's tools. The second investigation of the same condition adds a case rather than replacing what was there, and every write — by a run or by a person — snapshots what it displaced first.

![The sessions console](img/hookprobe-sessions.png)

![Every tool call of an investigation, as it happens](img/hookprobe-live-feed.png)

![The diagnostic runbook a run distilled for itself](img/hookprobe-skills.png)

---

## Running an agent where it can cost you something

An agent here is an untrusted network service that spends money, reads text an attacker may have written, and holds credentials. Everything below exists because one of those three is true.

| Security Layer | Technical Control |
| --- | --- |
| **Signed Handovers** | Every door verifies a timestamped HMAC; every node has its own secret, budget and guards |
| **Closed Tool Allowlist** | `HOOKPROBE_MCP_TOOLS` names the MCP tools an instance may call, and **empty denies all of them**. Mounting a server does not grant its tools — no server is read-only just because you wanted it to be, and a chat server ships `send_message` beside `search_messages` |
| **Closed Verdict Vocabulary** | A verdict that can steer a route is picked from a set the operator declared, never written free-hand into one |
| **Constructed Read-Only** | Mutating verbs refused before they run (`aws` is refused unless the command *reads*), read-only credentials as the real boundary, and a hook that stops a run editing what steers the next one. Declared per node (`HOOKPROBE_BASH_GUARD`), measured against the mounted credentials at startup, and widened only on purpose — the work node that a person hands a plan to runs `danger-only`, where the credential is the whole blast radius |
| **Financial Ceilings** | Past the budget, new autonomous runs are refused — and each refusal reports itself instead of going quiet |
| **Graph Verifier** | `GET /topology` renders doors, stages and exits from config alone, and names the hazards the shape implies: a door nothing can reach, an exit nothing feeds, a return door that can fall through to a wildcard and hand a brain its own output |
| **Replay Ledger** | `GET /trace/{id}` replays both directions of every hop — bodies only, never headers, because headers carry signatures and tokens |

The honest version of all of it, including what each boundary does **not** stop, is [docs/containment.md](https://github.com/itswl/hookstack/blob/main/docs/containment.md). A guard that is only described by what it catches gets trusted for things it never claimed.

### Not everything through the pipe is an alert

The judge earns its keep on alerts — severity, recovery, flapping, the five routes ordered by cost. A source that has ALREADY decided (a watcher forwarding "this needs a person", a system that only emits what matters) gains nothing from being judged again, and would be judged in a vocabulary that does not fit it.

So a route may be terminal: a signed door of your own, straight to the channel you named, past the judge. It still gets the pipe's ledger, retries and dead letters — the parts that are about delivery rather than about content.

The investigator is still reachable from there, and asks a different question when the event is work rather than a fault: what exists now, what is missing, the steps, how the result would be verified — and what it could not see, named rather than guessed at. It proposes nothing for execution either way.

### One codebase, two very different graphs

The repository runs two deployments that share every line of service code. One carries alerts from a monitoring platform through three judges to a card. The other carries an operator's own work signals — chat and tickets — through a watcher to two different chats, with a planner on the branch that is actually work. Neither needed a line of Python the other did not, and the four places they diverge each have a reason: [docs/deployments.md](https://github.com/itswl/hookstack/blob/main/docs/deployments.md).

---

## Product Roadmap & Advanced Patterns

1.  **Proposal-based Auto-healing (Remediation Loops) — shipped in its first form.** The investigator proposes; the card carries cryptographically signed `[Approve]` / `[Reject]` buttons; an approve runs each step against an allowlist, as an argv and never through a shell, on a node whose posture is `danger-only` and whose credential is the whole blast radius — read back by the startup posture check ([how](https://github.com/itswl/hookstack/blob/main/hookprobe/README.md#security-model)). What is still open is the credential: no deployed node holds a write principal yet, so the loop has been rehearsed end to end with read-only credentials — the ceremony is proven, the effect is not.
2.  **SRE-specific RLHF (Self-Evolution):** Capturing card clicks ("Actually mattered", "Snooze") to automatically assemble a localized reinforcement learning dataset. This dataset is fed into automated prompt-tuning loops or local model fine-tuning.
3.  **Local Model Validation (vLLM/Ollama):** The judge already speaks to any OpenAI-compatible base, so a zero-cost, fully offline Qwen/Llama brain is configuration today ([how](https://github.com/itswl/hookstack/blob/main/hookjudge/README.md#local-and-self-hosted-models)). What is not yet done is the measurement: the golden set has never been run against a 7B model, and `missed` / `false_quiet` on one are the numbers an air-gapped deployment needs before it trusts it.
4.  **Dual-Brain Shadow Audit Views:** Running multiple decision prompts or model comparison arms in parallel, allowing SRE teams to audit model decision drift at production scales before promoting changes to production.

---

## The whole family

| Component | Role | Deliberately does NOT |
| --- | --- | --- |
| **hookrelay** | the pipe — adapts every upstream dialect in and every channel format out, and accounts for all of it | understand content, or judge |
| **hookjudge** | the judge — one event in, one verdict out, five routes ordered by cost | render cards, or know channels |
| **hookprobe** | the investigator — one agent run per event that earns it, read-only by default, answering *what broke* for an alert and *how would this be done* for a work item | receive alerts, or send notifications |

```
upstream alert sources (Grafana / Alertmanager / cloud monitoring …)
      │
      ▼
  hookrelay :8100 ──► hookjudge :8200 ──► hookrelay ──► Feishu / DingTalk / WeCom
  (pipe: adapt+route+ledger) │ (judge: verdict+cost)   (formats and delivers)
                             │
                             └──► hookprobe :8088 ──► hookrelay ──► the same channels
                                  (investigator: read-only by default)  /hook/probe-notify

a source that already judged its own signal takes a terminal route instead:

  your source ──► hookrelay ──┬──► the channel you named   (no judge: it is decided)
  (signed door)               └──► hookprobe :8088         (only if it says so)
```

![hookrelay's ledger: every message accounted for, every delivery with an outcome](img/hookrelay-ledger.png)

![hookjudge's status page: four verdicts with their routes](img/hookjudge-status.png)

Every screenshot above comes from one local Docker run started from nothing — not mockups.

---

## How each piece works

One shape — **pipe, brain, investigator** — and each component has exactly one job. What is inside each, and why.

### hookrelay — the pipe

- **Adapters.** Declarative per-source config lifts `title`, `body`, `level` and fields out of any upstream payload (Alertmanager, Grafana, a bare webhook) and normalizes the level, so nothing downstream learns a vendor's dialect.
- **A storm fuse at every door.** Per-source volume protection in two stages: past the threshold an event is still *recorded* (`skipped · storm_suppressed`, count in its trace) but walks no pipeline and reaches no channel or paid brain; past 10× it is refused with a 429 before touching storage, because at that volume the ledger itself is what needs protecting. Process-local on purpose: a fuse protects, a ledger accounts.
- **A per-channel circuit breaker.** When a channel is wholly down (Feishu unreachable, a bot revoked), the breaker opens after consecutive failures and *defers* every delivery for it rather than burning their attempt budgets against a wall; after a cooldown exactly one probe delivery goes through, and its result closes or re-opens the breaker.
- **A delivery worker built around rate limits.** Parallel *across* channels so one hung endpoint cannot head-of-line block the rest; serial *within* a channel so the per-minute limit counts what was actually sent. Failures back off from 30 s, doubling to a 600 s cap, then dead-letter — with the ledger saying why.
- **Signed card actions.** A button's payload is HMAC-signed with `action_secret` before the card leaves; a press is accepted back through the door only if the signature verifies, so nothing on the network can forge a person's click.

### hookjudge — the brain

It answers one question — *does a person need to act on this now?* — and knows nothing about cards or channels. Five routes, tried in cost order, and the order **is** the cost policy:

| route | cost | when |
| --- | --- | --- |
| `recovery` | free | the condition ended: inherit the verdict its firing was given, never re-analyse the past |
| `reuse` | free | the same identity was judged inside the window — a storm is one condition restated |
| `rule-reuse` | free | this alert *rule*'s last AI verdict answers again (measured: 28 of 29 rules answered identically every time) |
| `ai` | paid | a model reads it, under a versioned prompt that must return strict JSON |
| `rule` | free | the model was unavailable, over budget or answered unusably: bilingual keyword rules decide, and the verdict *says so* in `degraded_reason` — a hidden downgrade is worse than a missing verdict |

When the model fails for a reason a person must fix — a dead key, no credit, a hard quota — the judge raises one rate-limited alarm instead of silently judging everything by keywords until somebody reads the ledger.

### hookprobe — the investigator

When a verdict earns it (critical/high), the pipe hands a copy of the event to hookprobe, which runs **one agent session, read-only by default,** on the Claude Agent SDK (bash, MCP servers, `SKILL.md` skills) and serves the report to whoever polls — an OpenClaw-compatible contract, so a client already pointed at that gateway switches by changing a URL. Read-only is *constructed*, in layers that fail differently, and it is *measured*, because a boundary that lives in a mounted credential can drift without a diff:

1. **Credentials are the real boundary.** The kubeconfig and cloud keys mounted into the container are read-only principals. If every other layer failed, the cluster and the cloud would still refuse.
2. **A bash guard refuses mutating verbs before they run.** A PreToolUse hook denies `kubectl delete/apply`, `helm` changes, `systemctl` writes and their kin. For `aws`, whose CLI is too large to blacklist, the list is *inverted*: anything that is not a known read verb (`describe`, `get`, `list`, …) is refused, so a new mutating API cannot slip through by being new.
3. **The input surface is fingerprinted.** An alert body may carry an indirect injection telling the agent to edit `.claude/`, a skill or `CLAUDE.md` so the instruction outlives the run. Every steering file is hashed before and after each run; any change the operator did not make is reported as `input_changes`, and the hook refuses the write in the first place — two mechanisms, because they fail differently.
4. **The posture is declared, measured, and widened only on purpose.** `HOOKPROBE_BASH_GUARD` says what a node is for — `readonly` for anything that faces an event door, `danger-only` for the one node a person hands work to. At startup the service asks the mounted credentials what they can actually do and compares: a node wider than it declared refuses to start, a `danger-only` node records its blast radius, and the verdict sits at the top of every run's audit. Under `danger-only` the guard keeps only what no credential scope can undo (`rm -rf`, `mkfs`, `terraform destroy`, namespace-wide `kubectl delete`); the credential bounds the rest, and nothing reaches that node without a signed click — a plan handed off from a card, or a remediation approved step by step against an allowlist.

### lark-bridge — the sidecar the pipe would not become

A custom bot can only *send*, so the buttons on its cards have nowhere to call back. The bridge exists for that return path: it **dials out** to Lark over a long connection to receive button presses and forwards each to hookrelay's signed card-action door. Because the connection is outbound, the alerting network opens no inbound port — hookrelay's public front door was deliberately rolled back, and this does not reopen it. A sidecar rather than a pipe plugin because an IM platform's auth, token refresh and websocket dialect are none of the pipe's four pillars, and the pipe caps its own size.

---

## Read more

*   [Full narrative overview](https://github.com/itswl/hookstack/blob/main/OVERVIEW.md)
*   [hookprobe reference](https://github.com/itswl/hookstack/blob/main/hookprobe/README.md)
*   [Running the whole family](https://github.com/itswl/hookstack/blob/main/STACK.md)
*   [WebhookWise](https://github.com/itswl/WebhookWise) — the self-hosted alerting platform these grew out of
