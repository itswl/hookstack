# hookstack

[![ci](https://github.com/itswl/hookstack/actions/workflows/ci.yml/badge.svg)](https://github.com/itswl/hookstack/actions/workflows/ci.yml)
[![ci-hookjudge](https://github.com/itswl/hookstack/actions/workflows/ci-hookjudge.yml/badge.svg)](https://github.com/itswl/hookstack/actions/workflows/ci-hookjudge.yml)
[![ci-hookprobe](https://github.com/itswl/hookstack/actions/workflows/ci-hookprobe.yml/badge.svg)](https://github.com/itswl/hookstack/actions/workflows/ci-hookprobe.yml)

Run AI agents in production with absolute financial accountability and structural containment.

The architecture is content-blind and decoupled: **something produces signals, a pipe carries them and accounts for every hop, specialized nodes decide or investigate, and what survives reaches a person.** Wired entirely by configuration rather than code.

Every handover is cryptographically signed, retried, backoff-scheduled, and replayable. Every agent runs with restricted credentials, budget ceilings, and a closed list of permitted actions.

**Alerts are the instance this was worn in on**, and most examples speak that dialect. They are not the shape. The second deployment in this repository carries an operator's own work signals — chat and tickets, through a watcher and a planner, into two different chats — and shares every line of the same code with the first ([docs/deployments.md](docs/deployments.md)).

Narrative overview with screenshots: [OVERVIEW.md](OVERVIEW.md). MIT licensed.

## Core Value Proposition

Hookstack optimizes two scarce resources a stream of signals actually spends: **a person's attention** and **the LLM model bill**.

*   **Cost Minimization Policy:** A verdict answers *does a person need to act now*. High-priority verdicts are cached and re-served to absorb alert storms; recovery events inherit prior firing states for free instead of querying a model again.
*   **Defense-in-Depth Agent Security:** Agents spend money, read text an attacker may have written, and hold keys. Hookstack treats them as untrusted. Mutating verbs are refused before they run; directory-fingerprinting stops indirect prompt injections from hijacking standing instructions; and public-facing routes are rolled back in favor of outbound sidecars.
*   **Programmatic Design Enforcement:** A suite of AST-based and graph-based checks in `gate.sh` ensures that duplication doesn't drift, code sizes stay within strict README budgets, and routing configs contain no feedback loops or dead-ends.

## Ten minutes, no keys, no bill

```bash
curl -fsSLO https://raw.githubusercontent.com/itswl/hookstack/main/docker-compose.quickstart.yml
docker compose -f docker-compose.quickstart.yml up -d   # pipe + judge + stub model + readable sink
bash <(curl -fsSL https://raw.githubusercontent.com/itswl/hookstack/main/scripts/demo.sh)
```

No checkout and no build: everything runs from published images, and the stub model and the sink ship inside them. To hack on it instead, clone and `docker compose up -d --build` — that file builds from source and is the one the gate tests.

Real credentials in `.env` make the stub step aside, and `--profile probe` adds the investigator.

## The Three Services

```
upstreams ──► hookrelay ──► hookjudge ──► hookrelay ──► lark / dingtalk / wecom / webhook
              (adapts)  │   (judges)     (formats)
                        └─► hookprobe ──► hookrelay ──► the same channels
                            (investigates critical/high; opt-in, see STACK.md)
```

| Service | Stated Line Budget | Role & Architectural Purpose | Deliberately does NOT |
| --- | --- | --- | --- |
| [`hookrelay/`](hookrelay) | **5,400 lines** (actual: ~5,300) | **The Pipe.** Adapts upstream webhooks, handles backoff retries, implements circuit breakers / storm fuses, replaces button values with signed action tokens, and hosts the SQLite ledger. | Understand message content, or make autonomous judgments. |
| [`hookjudge/`](hookjudge) | **3,350 lines** (actual: ~3,280) | **The Judge.** One event in, one verdict out. Implements the cost policy as five routes tried in cost order: `recovery` ──► `reuse` ──► `rule-reuse` ──► `ai` ──► `rule` (keyword floor). | Render platform-specific cards, or hold channel credentials. |
| [`hookprobe/`](hookprobe) | **Uncapped** (actual: ~8,400) | **The Investigator.** Runs a single Claude Agent SDK run per deep-analysis task — read-only by default, and measured so at startup; a `danger-only` posture exists for the one node a person hands work to. Exposes an OpenClaw-compatible triggers endpoint. | Receive raw alerts directly, or send downstream messages. |

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

## Product Roadmap & Advanced Patterns

1.  **Proposal-based Auto-healing (Remediation Loops) — shipped in its first form.** The investigator proposes; the card carries cryptographically signed `[Approve]` / `[Reject]` buttons; an approve runs each step against an allowlist, as an argv and never through a shell, on a node whose posture is `danger-only` and whose credential is the whole blast radius — read back by the startup posture check ([how](hookprobe/README.md#security-model)). What is still open is the credential: no deployed node holds a write principal yet, so the loop has been rehearsed end to end with read-only credentials — the ceremony is proven, the effect is not.
2.  **SRE-specific RLHF (Self-Evolution):** Capturing card clicks ("Actually mattered", "Snooze") to automatically assemble a localized reinforcement learning dataset. This dataset is fed into automated prompt-tuning loops or local model fine-tuning.
3.  **Local Model Validation (vLLM/Ollama):** The judge already speaks to any OpenAI-compatible base, so a zero-cost, fully offline Qwen/Llama brain is configuration today ([how](hookjudge/README.md#local-and-self-hosted-models)). What is not yet done is the measurement: the golden set has never been run against a 7B model, and `missed` / `false_quiet` on one are the numbers an air-gapped deployment needs before it trusts it.
4.  **Dual-Brain Shadow Audit Views:** Running multiple decision prompts or model comparison arms in parallel, allowing SRE teams to audit model decision drift at production scales before promoting changes to production.

## Developer & Verification Docs

*   [`docs/containment.md`](docs/containment.md) — Fourteen structural security boundaries, and exactly what each does **not** stop.
*   [`docs/deployments.md`](docs/deployments.md) — Two deployments sharing every line of code but agreeing on nothing: alerts vs work timers.
*   [`STACK.md`](STACK.md) — Local runbook to drive the whole cost-saving pipeline step-by-step.
*   [`CONTRIBUTING.md`](CONTRIBUTING.md) — Per-service gates, AST copy validations, and general SDLC workflows.

Each service has its own gate, Dockerfile and CI workflow. For a change that touches more than one, `bash scripts/gate.sh` runs all of them plus the stack checks. Always read its verdict; never chain it.
