# hookstack

[![ci](https://github.com/itswl/hookstack/actions/workflows/ci.yml/badge.svg)](https://github.com/itswl/hookstack/actions/workflows/ci.yml)
[![ci-hookjudge](https://github.com/itswl/hookstack/actions/workflows/ci-hookjudge.yml/badge.svg)](https://github.com/itswl/hookstack/actions/workflows/ci-hookjudge.yml)
[![ci-hookprobe](https://github.com/itswl/hookstack/actions/workflows/ci-hookprobe.yml/badge.svg)](https://github.com/itswl/hookstack/actions/workflows/ci-hookprobe.yml)

Two services that split one job in half, and an investigator for the alerts
where a verdict is not enough. 中文总览（含全家截图）：[README.zh.md](README.zh.md)。

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

The split is the point: a brain that renders Feishu cards has to know Feishu's
card schema, then WeCom's, then DingTalk's. That work belongs to the pipe, and
moving it there is what lets a brain be replaced — or compared against another
one — without touching either edge. hookjudge is deliberately the smallest
brain that can hold up its end of that bargain.

hookprobe answers a different question than the judge: not "does this deserve
attention" but "what actually happened". One unattended read-only agent
session per alert, with none of the gateway product usually wrapped around
that capability — it speaks the OpenClaw trigger/poll dialect, so anything
that already integrates such a gateway as an analysis backend can switch by
changing a URL. In this stack it is opt-in (see STACK.md): it needs a real
model key to be worth starting.

Each service has its own gate, its own Dockerfile and its own CI workflow, so a
change to one does not queue the other's jobs.

---

## Layout

The services are laid out the same way, each self-contained — its own package,
tests, gate, Dockerfile and CI workflow. Nothing at the root belongs to one
service more than the others.

```
hookstack/
├── docker-compose.yml        the pipe + brain demo, one command
├── deploy/                   the three services together, real credentials
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
zero-credential demo. All are run from the repo root with
`docker compose --env-file .env -f <file> up -d --build`; the two
`docker-compose.prod.yml` files under `hookrelay/deploy/` and
`hookprobe/deploy/` are the shapes joined to a platform's own docker network.

Each gate is run from its own directory:

```bash
cd hookrelay && bash scripts/gate.sh
cd hookjudge && bash scripts/gate.sh
cd hookprobe && bash scripts/gate.sh
```

Neither gate can see the stack, so there is a third check that can:

```bash
bash scripts/stack-smoke.sh
```

Four workflows, and between them nothing at the root is uncovered:
`ci` (pipe) · `ci-hookjudge` (brain) · `ci-hookprobe` (investigator) ·
`ci-stack` (the demo pair together, plus the docs and the compose files).
