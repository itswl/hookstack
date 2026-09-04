# hookstack

[![ci](https://github.com/itswl/hookstack/actions/workflows/ci.yml/badge.svg)](https://github.com/itswl/hookstack/actions/workflows/ci.yml)
[![ci-hookjudge](https://github.com/itswl/hookstack/actions/workflows/ci-hookjudge.yml/badge.svg)](https://github.com/itswl/hookstack/actions/workflows/ci-hookjudge.yml)
[![ci-hookprobe](https://github.com/itswl/hookstack/actions/workflows/ci-hookprobe.yml/badge.svg)](https://github.com/itswl/hookstack/actions/workflows/ci-hookprobe.yml)

Run agents in production and be able to account for them afterwards.

Three services that split alert handling — a content-blind pipe, a judge, and a
read-only investigator — wired by a config file rather than by code. What makes
it more than a router is what surrounds each handover: every hop is signed,
retried, dead-lettered, priced and replayable, and every agent runs behind a
credential, a budget and a closed list of what it may do.

Two deployments in this repository share every line of that code and agree on
almost nothing else — one carries alerts, the other carries an operator's own
work signals ([docs/deployments.md](docs/deployments.md)). Narrative overview
with screenshots: [OVERVIEW.md](OVERVIEW.md). MIT licensed.

## What it optimizes

Not response time and not breadth of integrations — the two scarce resources an
alert stream actually spends: **a person's attention** and **the model bill**.
A verdict answers *does a person need to act now*, and delivery routes on the
answer; a paid verdict is reused across restatements instead of bought again; a
condition ruled not worth investigating answers its re-fires from the runbook
earlier investigations wrote, at no cost. Every card and every paid call lands
in a ledger, with a regret counter over what the quiet swallowed. The mechanics
of each loop are in [OVERVIEW.md](OVERVIEW.md).

Anything irreversible — memory, remediation, a runbook a human relies on — keeps
a write gate, proposes rather than acts, and stays reversible in one call.

## Agents are treated as untrusted services

An agent here spends money, reads text an attacker may have written, and holds
credentials. So every handover is signed and priced, every node gets its own
secret, budget and guards, and what an agent may *do* and may *conclude* are
both closed lists an operator writes down — an empty one denies everything.
Mutating verbs are refused before they run; `GET /topology` shows the graph
before you change it and `GET /trace/{id}` replays both directions of every hop
after.

[docs/containment.md](docs/containment.md) is the honest version: thirteen
boundaries, each with a column for what it does **not** stop. None of them
assume the agent is hookprobe — `assert_dialect.py` checks the node contract
from the bus side, so replacing the reference implementation is a command
rather than a claim.

## Ten minutes, no keys, no bill

```bash
curl -fsSLO https://raw.githubusercontent.com/itswl/hookstack/main/docker-compose.quickstart.yml
docker compose -f docker-compose.quickstart.yml up -d   # pipe + judge + stub model + readable sink
bash <(curl -fsSL https://raw.githubusercontent.com/itswl/hookstack/main/scripts/demo.sh)
```

No checkout and no build: everything runs from published images, and the stub
model and the sink ship inside them. To hack on it instead, clone and
`docker compose up -d --build` — that file builds from source and is the one
the gate tests.

It posts a fresh alert, the same alert again (reused, no call), its recovery,
and a different one — then prints the judge's ledger with routes and costs.
Boards at `:8100` and `:8200`; what an operator would have received is in
`docker compose logs -f sink`. Real credentials in `.env` make the stub step
aside, and `--profile probe` adds the investigator.

## The three services

```
upstreams ──► hookrelay ──► hookjudge ──► hookrelay ──► lark / dingtalk / wecom / webhook
              (adapts)  │   (judges)     (formats)
                        └─► hookprobe ──► hookrelay ──► the same channels
                            (investigates critical/high; opt-in, see STACK.md)
```

| | what it does | docs |
| --- | --- | --- |
| [`hookrelay/`](hookrelay) | the pipe. Adapts every upstream dialect in, builds every downstream format out, keeps the ledger. Content-blind. | [hookrelay/README.md](hookrelay/README.md) |
| [`hookjudge/`](hookjudge) | the brain. Judges and nothing else — one inbound shape, one outbound address. | [hookjudge/README.md](hookjudge/README.md) |
| [`hookprobe/`](hookprobe) | the investigator. One read-only agent run per deep-analysis task, with a sessions page to keep asking. Replaces a full OpenClaw gateway. | [hookprobe/README.md](hookprobe/README.md) |
| pipe + brain | run the demo pair locally in one command | [STACK.md](STACK.md) |
| all three boards | one design language, one refresh clock | [docs/design-language.md](docs/design-language.md) |
| every boundary | what stops an agent that costs money, reads attacker-influenced text and holds credentials — and, in its own column, what each boundary does NOT stop | [docs/containment.md](docs/containment.md) |
| two deployments | the same services wired into two graphs that agree on almost nothing — alerts, and an operator's own attention — plus the four decisions that differ and why | [docs/deployments.md](docs/deployments.md) |
| decisions | why it is done this way, and why not the obvious way | [.agents/README.md](.agents/README.md) |

The split is the point: a brain that renders Feishu cards has to know Feishu's
card schema, then WeCom's, then DingTalk's. That work belongs to the pipe, and
moving it there is what lets a brain be replaced — or compared against another
one — without touching either edge. hookprobe answers a different question than
the judge: not "does this deserve attention" but "what actually happened".

Each service has its own gate, Dockerfile and CI workflow. For a change that
touches more than one, `bash scripts/gate.sh` runs all of them plus the stack
checks — read its verdict, never chain it. Layout, per-service gates and the
rest of the working notes: [CONTRIBUTING.md](CONTRIBUTING.md).
