# hookstack

[![ci](https://github.com/itswl/hookstack/actions/workflows/ci.yml/badge.svg)](https://github.com/itswl/hookstack/actions/workflows/ci.yml)
[![ci-hookjudge](https://github.com/itswl/hookstack/actions/workflows/ci-hookjudge.yml/badge.svg)](https://github.com/itswl/hookstack/actions/workflows/ci-hookjudge.yml)

Two services that split one job in half.

```
upstreams ──► hookrelay ──► hookjudge ──► hookrelay ──► lark / dingtalk / wecom / webhook
              (adapts)      (judges)     (formats)
```

| | what it does | docs |
| --- | --- | --- |
| [`hookrelay/`](hookrelay) | the pipe. Adapts every upstream dialect in, builds every downstream format out, keeps the ledger. Content-blind. | [hookrelay/README.md](hookrelay/README.md) |
| [`hookjudge/`](hookjudge) | the brain. Judges and nothing else — one inbound shape, one outbound address. | [hookjudge/README.md](hookjudge/README.md) |
| both | run the family locally in one command | [STACK.md](STACK.md) |

The split is the point: a brain that renders Feishu cards has to know Feishu's
card schema, then WeCom's, then DingTalk's. That work belongs to the pipe, and
moving it there is what lets a brain be replaced — or compared against another
one — without touching either edge. WebhookWise is the other brain, and it
stays comprehensive; hookjudge's minimalism is the contrast, not a criticism.

Each service has its own gate, its own Dockerfile and its own CI workflow, so a
change to one does not queue the other's jobs.

---

## Layout

Both services are laid out the same way, each self-contained — its own package,
tests, gate, Dockerfile and CI workflow. Nothing at the root belongs to one
service more than the other.

```
hookstack/
├── docker-compose.yml        the whole family, one command
├── STACK.md
├── hookrelay/               the pipe
│   ├── hookrelay/           package
│   ├── tests/  scripts/  examples/  docs/  deploy/
│   ├── Dockerfile  pyproject.toml  requirements.txt
│   └── README.md
├── hookjudge/               the brain
│   ├── hookjudge/           package
│   ├── tests/  scripts/
│   ├── Dockerfile  pyproject.toml  requirements.txt
│   └── README.md
└── .github/workflows/       ci.yml (pipe) · ci-hookjudge.yml (brain)
```

Each gate is run from its own directory:

```bash
cd hookrelay && bash scripts/gate.sh
cd hookjudge && bash scripts/gate.sh
```
