# hookstack

[![ci](https://github.com/itswl/hookstack/actions/workflows/ci.yml/badge.svg)](https://github.com/itswl/hookstack/actions/workflows/ci.yml)
[![ci-hookjudge](https://github.com/itswl/hookstack/actions/workflows/ci-hookjudge.yml/badge.svg)](https://github.com/itswl/hookstack/actions/workflows/ci-hookjudge.yml)
[![ci-hookprobe](https://github.com/itswl/hookstack/actions/workflows/ci-hookprobe.yml/badge.svg)](https://github.com/itswl/hookstack/actions/workflows/ci-hookprobe.yml)

Two services that split one job in half, and an investigator for the alerts
where a verdict is not enough. Narrative overview with screenshots of all three:
[OVERVIEW.md](OVERVIEW.md). MIT licensed.

## What it optimizes

Not response time, and not breadth of integrations — the two scarce resources
an alert stream actually spends: **a person's attention** and **the model
bill**. Every card and every paid call lands in a ledger, and the system's job
is to spend both only where they change an outcome, then prove it:

- a verdict answers *does a person need to act now* — and delivery routes on
  the answer, so a "no" never interrupts anyone (while staying on every board);
- a paid verdict is reused across restatements instead of bought again;
- a condition ruled *not worth investigating* answers its re-fires from the
  runbook earlier investigations wrote, at no cost, and still re-verifies
  itself on a schedule;
- what a person never confirmed is inferred — and permanently labelled as
  inference, never blended with a human's ruling.

Each loop turns yesterday's spend into tomorrow's savings, so the curve to
watch is the bend: cards per condition falling, dollars per answered incident
falling, with a regret counter standing guard over everything the quiet
swallowed. The unattended posture has one hard edge: anything irreversible —
memory, remediation, a runbook a human relies on — keeps a write gate,
proposes rather than acts, and stays reversible in one call.

## Where this sits in an AI-native SDLC

Anthropic's [AI-native SDLC playbook](https://claude.com/blog/the-ai-native-sdlc-playbook)
names the stage this family is built for — **Maintain**: cheap deterministic
answers before a model is paid, an investigator only for the alerts that
earned one, remediation proposed rather than applied, every decision in a
ledger a person can audit later. That posture is enforced here rather than
described: the verdict routes are ordered so most events never reach a paid
call, the write-gates are tested, and a prompt change that under-calls a
golden incident does not deploy.

[How Anthropic secures its own AI-native SDLC](https://claude.com/blog/how-anthropic-secures-its-ai-native-software-development-lifecycle)
treats agents as monitored actors rather than trusted authors. Same side
taken here, for the same reason: the investigator runs read-only, cannot edit
what steers its next run, its runbooks are written by the service and never
through its own tools — and a red-team run drives injections at the memory
path on the deploy host before an operator is asked to trust it.

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

The demo posts a fresh alert (judged by the stub model), the same alert again
(reused, no call), its recovery (reuses the firing's verdict), and a different
one — then prints the judge's ledger with routes and costs. Boards:
`http://127.0.0.1:8100` (pipe) and `:8200` (judge); what an operator would
have received: `docker compose logs -f sink`. Real credentials in `.env` make
the stub step aside; `--profile probe` adds the investigator (needs a model
key). Tagged releases publish images for both amd64 and arm64:
`ghcr.io/itswl/{hookrelay,hookjudge,hookprobe}`.

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
one — without touching either edge. hookjudge is deliberately the smallest
brain that can hold up its end of that bargain, and "smallest" is a number
under a ceiling rather than an adjective: the pipe and the brain each state a
source-line budget in their README, and `scripts/assert_weight.py` fails the
gate when either drifts past it. hookprobe is uncapped on purpose — it carries
Node and the Claude CLI, and being the heavy one is its job.

hookprobe answers a different question than the judge: not "does this deserve
attention" but "what actually happened". One unattended read-only agent
session per alert, with none of the gateway product usually wrapped around
that capability — it speaks the OpenClaw trigger/poll dialect, so anything
that already integrates such a gateway as an analysis backend can switch by
changing a URL. In this stack it is opt-in (see STACK.md): it needs a real
model key to be worth starting.

Each service has its own gate, its own Dockerfile and its own CI workflow, so a
change to one does not queue the other's jobs. For a change that touches more
than one, `bash scripts/gate.sh` runs every component's gate plus the stack
checks in one command.

---

## Layout

The services are laid out the same way, each self-contained — its own package,
tests, gate, Dockerfile and CI workflow. Nothing at the root belongs to one
service more than the others.

```
hookstack/
├── docker-compose.quickstart.yml   published images, no checkout, no build
├── docker-compose.yml        the same demo built from source (what the gate tests)
├── deploy/                   real deployments, real credentials — see docs/deployments.md
│   ├── docker-compose.yml    the three services together
│   ├── *.shadow.yml + shadow.yaml   alerts: platform -> three judges -> card
│   └── *.work.yml   + work.yaml     work signals: timer -> watcher -> two chats
├── STACK.md
├── hookrelay/               the pipe
│   ├── hookrelay/           package
│   ├── tests/  scripts/  examples/  docs/  deploy/
│   ├── Dockerfile  pyproject.toml  requirements.txt
│   └── README.md
├── hookjudge/               the brain
│   ├── hookjudge/           package
│   ├── tests/  scripts/  deploy/
│   ├── Dockerfile  pyproject.toml  requirements.txt
│   └── README.md
├── hookprobe/               the investigator
│   ├── hookprobe/           package (incl. the sessions page)
│   ├── tests/  scripts/  deploy/
│   ├── Dockerfile  pyproject.toml  requirements.txt
│   └── README.md
└── .github/workflows/       ci.yml (pipe) · ci-hookjudge.yml (brain) · ci-hookprobe.yml (investigator)
```

Every service also deploys the same way: `<service>/deploy/docker-compose.yml`
runs it standalone, `deploy/docker-compose.yml` at the root runs the three
together with real credentials, and the root `docker-compose.yml` stays the
zero-credential demo. The two named deployments beside it — `shadow` and
`work` — are the same services wired into different graphs, and the pair is
worth reading as a unit: [docs/deployments.md](docs/deployments.md). All are run from the repo root with
`docker compose --env-file .env -f <file> up -d --build`; the two
`docker-compose.prod.yml` files under `hookrelay/deploy/` and
`hookprobe/deploy/` are the shapes joined to a platform's own docker network.

Each gate is run from its own directory, against that service's own venv — the
tool versions are pinned in its `requirements.txt`, which is what keeps the gate
and CI from disagreeing:

```bash
cd hookprobe
python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt  # once
bash scripts/gate.sh
```

```bash
cd hookrelay && bash scripts/gate.sh
cd hookjudge && bash scripts/gate.sh
cd hookprobe && bash scripts/gate.sh
```

Neither gate can see the stack, so there is a third check that can:

```bash
bash scripts/stack-smoke.sh
```

It also checks what no service's own gate can: that the three boards still share
one design language, and that every decision record under
[`.agents/notes/`](.agents/README.md) keeps its shape. The `rejected/` bucket
there is worth reading before proposing something — several obvious ideas have
already been tried and removed.

Four workflows, and between them nothing at the root is uncovered:
`ci` (pipe) · `ci-hookjudge` (brain) · `ci-hookprobe` (investigator) ·
`ci-stack` (the demo pair together, plus the docs and the compose files).
