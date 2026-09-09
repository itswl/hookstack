# hookstack

[![ci](https://github.com/itswl/hookstack/actions/workflows/ci.yml/badge.svg)](https://github.com/itswl/hookstack/actions/workflows/ci.yml)
[![ci-hookjudge](https://github.com/itswl/hookstack/actions/workflows/ci-hookjudge.yml/badge.svg)](https://github.com/itswl/hookstack/actions/workflows/ci-hookjudge.yml)
[![ci-hookprobe](https://github.com/itswl/hookstack/actions/workflows/ci-hookprobe.yml/badge.svg)](https://github.com/itswl/hookstack/actions/workflows/ci-hookprobe.yml)

**Turn the signals a team already has into agent work that actually finishes — tracked, approved, verified, and accounted for.**

hookstack is a work operations platform for agents. It takes the signals a team already has — alerts, webhooks, chat messages, tickets, timers, monitoring events, and a person simply asking — and turns each one into a piece of **work** that an agent carries to an end: it picks the work up, keeps one session across every follow-up and every restart, plans what it would do, stops for a person on anything that writes, has its result verified, and leaves an audit record behind. Every hop is signed, every model call is priced, and one page a week says what the machine spent and what it saved — with the numbers a person can go and check.

Built for SRE, platform and operations teams who want agents doing real work without handing them the keys: agents run read-only by default and are measured against that at startup, behind scoped credentials, budget ceilings and a closed list of tools. Alerts are where it was hardened and they are still the busiest door — not the shape. The same pipe, on the same code, carries an operator's own work signals to a planner and a human-approved handoff ([two deployments, one codebase](docs/deployments.md)). What it does **not** have yet: teams as a first-class thing — one deployment is one workspace, the chat sender allowlist and the console token are the whole of "who", and there are no roles, no per-person permissions and no tenancy.

Narrative overview with screenshots: [OVERVIEW.md](OVERVIEW.md). MIT licensed.

## The shape of the work

Seven steps, and each one names the thing that does it — so the positioning above is checkable rather than a claim.

| step | what does it |
|---|---|
| **a signal arrives** | a door per source: any webhook, Alertmanager and Grafana shapes, a chat message, a timer, a watcher, or a person typing into the console |
| **an agent picks it up** | the route names the node; the caller says what KIND of thing it is — an alert to investigate, a task to plan, a question to answer |
| **the session persists** | one engine session per piece of work: a reply in the chat thread continues it for cents, a re-fire joins it instead of funding a cold start, and a restart resumes it rather than losing what it gathered |
| **a plan is executed** | the planner writes what it would do; a person hands it to the work runner, the one node not on `readonly` |
| **a person confirms** | every button is signed before the card leaves; a procedure runs only after an approval, step by step against an allowlist, as an argv and never through a shell — and expires after a day |
| **the result is verified** | a ruling, or the procedure's own steps exiting 0, or the condition itself ending — the board says which, and counts the work that closed with nobody stepping in |
| **the audit settles** | one page per event with every hop, digest, decision and human action; a flight recorder of every tool call the agent cannot edit; a timing waterfall per run; a weekly page over all three ledgers |

## What you get

- **Only the work worth doing.** A cheap judge decides whether a signal becomes work at all — *does a person need to act now?* — and a storm, a restatement or a recovery never buys a second verdict. Measured on 795 production alerts, 28 of 29 alert rules answered identically every time, so `rule-reuse` answers them without a model call.
- **One board, one row per piece of work.** However many runs it took, across however many nodes: what is blocked, what is in flight, what finished, what was verified, and the strict number — work that closed **without anyone stepping in**. Beside it, everything that waits on a person, and the agent's own line: which node this is, what it may do, and whether its reports are reaching the pipe.
- **A card a person can rule with.** Worth it, not worth it, silence, approve — each button signed before the card leaves, each click recorded, and every ruling fed back into the runbooks and the weekly page.
- **Investigations that leave something behind.** A finished run distils its own runbook; the next occurrence of the same condition adds a case, and a condition a person ruled *not worth it* answers its re-fires from that runbook for $0. A follow-up question on an open investigation costs about a tenth of a fresh run, measured.
- **Remediation with a person in the loop.** The investigator proposes; a signed approve runs each step against an allowlist, as an argv and never through a shell, on a node whose credential is the whole blast radius.
- **Every verdict priced, every week accounted for.** One page reads the three ledgers and puts measured beside counterfactual — what the routes avoided, what runbooks answered, how the live arm compared with its shadows — and says in words what it could not measure.
- **One page per event.** `/audit/{event_id}` shows every hop with digests, decision steps, deliveries and human actions; `/trace/{id}` replays the bodies; `/timeline` groups chains into incidents.
- **Agents you can contain.** Read-only by default and measured at startup, a closed tool allowlist, budget ceilings that refuse out loud, and twenty-two structural boundaries each documented with what it does **not** stop ([containment](docs/containment.md)).
- **Your model, your chat.** The investigator takes any Anthropic-dialect endpoint, the judge any OpenAI-compatible base including local models; shadow arms compare prompts or models on live traffic before anything is promoted. Cards reach the chat through one small protocol between the pipe and a per-platform bridge ([docs/bridge-protocol.md](docs/bridge-protocol.md)) — Feishu/Lark today, DingTalk and WeCom as a shipped plugin, another platform is another bridge — or go as signed JSON to any webhook; OpenTelemetry is on by default and received by the investigator itself — every run's waterfall is on its own page with no collector deployed, and forwarded untouched when you name one.

![hookrelay's ledger: every message accounted for, every delivery with an outcome](docs/img/hookrelay-ledger.png)

![hookrelay's timeline: one chain per alert — signal, verdict, report — with costs, one incident grouped, and an audit record opened](docs/img/hookrelay-timeline.png)

![hookjudge's status page: verdicts with their routes and what each cost](docs/img/hookjudge-status.png)

Screenshots are from one local Docker run started from nothing, not mockups — [OVERVIEW.md](OVERVIEW.md) has the rest of them.

## Ten minutes, no keys, no bill

```bash
curl -fsSLO https://raw.githubusercontent.com/itswl/hookstack/main/docker-compose.quickstart.yml
docker compose -f docker-compose.quickstart.yml up -d   # pipe + judge + stub model + readable sink
bash <(curl -fsSL https://raw.githubusercontent.com/itswl/hookstack/main/scripts/demo.sh)
```

No checkout and no build: everything runs from published images, and the stub model and the sink ship inside them. To hack on it instead, clone and `docker compose up -d --build` — that file builds from source and is the one the gate tests.

Real credentials in `.env` make the stub step aside, and `--profile probe` adds the investigator.

The published images are `0.3.0`, and they now run the current design: the pipe sends a neutral card model and `lark-bridge`, in webhook mode against the sink, renders it — no Lark account needed. Up to `0.2.0` the quickstart's pipe rendered the Feishu card itself, which is the one thing the published demo used to do differently from the source compose.

## The Three Services

One shape, wired by configuration rather than code: **something produces signals, a pipe carries them and accounts for every hop, specialized nodes decide or investigate, and what survives reaches a person.**

```
upstreams ──► hookrelay ──► hookjudge ──► hookrelay ──► chat bridge / webhook
              (adapts)  │   (judges)     (models)
                        └─► hookprobe ──► hookrelay ──► the same channels
                            (investigates critical/high; opt-in, see STACK.md)
```

| Service | What it does | Deliberately does NOT |
| --- | --- | --- |
| [`hookrelay/`](hookrelay) | **The Pipe.** Adapts upstream webhooks, handles backoff retries, implements circuit breakers / storm fuses, replaces button values with signed action tokens, and hosts the SQLite ledger. | Understand message content, or make autonomous judgments. |
| [`hookjudge/`](hookjudge) | **The Judge.** One event in, one verdict out. Implements the cost policy as five routes tried in cost order: `recovery` ──► `reuse` ──► `rule-reuse` ──► `ai` ──► `rule` (keyword floor). | Render platform-specific cards, or hold channel credentials. |
| [`hookprobe/`](hookprobe) | **The Investigator.** Runs a single Claude Agent SDK run per deep-analysis task — read-only by default, and measured so at startup; a `danger-only` posture exists for the one node a person hands work to. Exposes an OpenClaw-compatible triggers endpoint. | Receive raw alerts directly, or send downstream messages. |

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

A custom bot can only *send*, so the buttons on its cards have nowhere to call back — and a pipe that rendered every platform's card would have to know every platform. The bridge is where both problems live. The pipe speaks one small protocol to it ([docs/bridge-protocol.md](docs/bridge-protocol.md)): a **card model** — title, tone, summary, links, actions as plain facts — signed with the pipe's own scheme; the bridge renders the Feishu card, sends it as the application (or, in webhook mode, to a custom bot's URL), and hands back the platform's message id. It **dials out** to Lark over a long connection to receive button presses and thread replies and forwards each to hookrelay's signed doors, so the alerting network opens no inbound port — hookrelay's public front door stays closed. Nothing in the pipe, the judge or the investigator names the platform: another chat is another bridge, tested against the same fixture; DingTalk and WeCom markdown come from a shipped plugin. It is a sidecar rather than a pipe plugin because an IM platform's auth, token refresh and websocket dialect are none of the pipe's four jobs — receive, route, deliver, account — and the pipe caps its own size ([its own README](deploy/lark-bridge/README.md) covers the Lark app it needs and how to run it).

## Product Roadmap & Advanced Patterns

1.  **Proposal-based Auto-healing (Remediation Loops) — shipped in its first form.** The investigator proposes; the card carries cryptographically signed `[Approve]` / `[Reject]` buttons; an approve runs each step against an allowlist, as an argv and never through a shell, on a node whose posture is `danger-only` and whose credential is the whole blast radius — read back by the startup posture check ([how](hookprobe/README.md#security-model)). What is still open is the credential: no deployed node holds a write principal yet, so the loop has been rehearsed end to end with read-only credentials — the approval path is proven end to end, its effect on a live system is not yet.
2.  **SRE-specific RLHF (Self-Evolution):** Capturing card clicks ("Actually mattered", "Snooze") to automatically assemble a localized reinforcement learning dataset. This dataset is fed into automated prompt-tuning loops or local model fine-tuning.
3.  **Local Model Validation (vLLM/Ollama):** The judge already speaks to any OpenAI-compatible base, so a zero-cost, fully offline Qwen/Llama brain is configuration today ([how](hookjudge/README.md#local-and-self-hosted-models)). What is not yet done is the measurement: the golden set has never been run against a 7B model, and `missed` / `false_quiet` on one are the numbers an air-gapped deployment needs before it trusts it.
4.  **Dual-Brain Shadow Audit Views:** Running multiple decision prompts or model comparison arms in parallel, allowing SRE teams to audit model decision drift at production scales before promoting changes to production.

## Developer & Verification Docs

*   [`docs/containment.md`](docs/containment.md) — Twenty-two structural security boundaries, and exactly what each does **not** stop.
*   [`docs/deployments.md`](docs/deployments.md) — Two deployments sharing every line of code but agreeing on nothing: alerts vs work timers.
*   [`STACK.md`](STACK.md) — Local runbook to drive the whole cost-saving pipeline step-by-step.
*   [`CONTRIBUTING.md`](CONTRIBUTING.md) — Per-service gates, AST copy validations, and general SDLC workflows.

The pipe caps itself at 5,500 source lines and the judge at 3,350; the investigator is uncapped by design. `scripts/assert_weight.py` enforces both ceilings, and the rest of `gate.sh` is AST- and graph-based: pinned copies stay identical, routing configs have no dead ends or feedback loops, and the docs state the same counts the code defines.

Each service has its own gate, Dockerfile and CI workflow. For a change that touches more than one, `bash scripts/gate.sh` runs all of them plus the stack checks. Always read its verdict; never chain it.
