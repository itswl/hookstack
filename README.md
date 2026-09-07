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
| [`hookjudge/`](hookjudge) | **3,350 lines** (actual: ~3,280) | **The Judge.** One event in, one verdict out. Emplements the cost-saving path: `recovery` ──► `reuse` ──► `ai` ──► `rule` (keyword backup). | Render platform-specific cards, or hold channel credentials. |
| [`hookprobe/`](hookprobe) | **Uncapped** (actual: ~8,400) | **The Investigator.** Runs a single, read-only Claude Agent SDK run per deep-analysis task. Exposes an OpenClaw-compatible triggers endpoint. | Receive raw alerts directly, or send downstream messages. |

## Product Roadmap & Advanced Patterns

1.  **Proposal-based Auto-healing (Remediation Loops):** Moving from read-only diagnostics to "propose-and-execute" remediation. The agent proposes a self-healing script, rendered as an interactive card with cryptographically signed `[Approve]` / `[Reject]` buttons. Executive action only fires when a human authorizes the signed token.
2.  **SRE-specific RLHF (Self-Evolution):** Capturing card clicks ("Actually mattered", "Snooze") to automatically assemble a localized reinforcement learning dataset. This dataset is fed into automated prompt-tuning loops or local model fine-tuning.
3.  **Local Model (vLLM/Ollama) Integration:** Providing zero-cost, 100% offline Qwen/Llama decision brains that run entirely inside air-gapped corporate environments.
4.  **Dual-Brain Shadow Audit Views:** Running multiple decision prompts or model comparison arms in parallel, allowing SRE teams to audit model decision drift at production scales before promoting changes to production.

## Developer & Verification Docs

*   [`docs/containment.md`](docs/containment.md) — Thirteen structural security boundaries, and exactly what each does **not** stop.
*   [`docs/deployments.md`](docs/deployments.md) — Two deployments sharing every line of code but agreeing on nothing: alerts vs work timers.
*   [`STACK.md`](STACK.md) — Local runbook to drive the whole cost-saving pipeline step-by-step.
*   [`CONTRIBUTING.md`](CONTRIBUTING.md) — Per-service gates, AST copy validations, and general SDLC workflows.

Each service has its own gate, Dockerfile and CI workflow. For a change that touches more than one, `bash scripts/gate.sh` runs all of them plus the stack checks. Always read its verdict; never chain it.
