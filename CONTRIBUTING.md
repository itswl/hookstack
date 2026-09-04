# Working on hookstack

Everything here used to sit on the README's front page, where it answered a
question nobody arriving has yet: how the repository is arranged and how to run
its checks. Read [AGENTS.md](AGENTS.md) first if you are changing anything — it
holds the rules that were each paid for once.

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

## The one rule that is not about layout

Run `bash scripts/gate.sh` as its own step and READ the verdict before any
commit or push. Never chain it: `&&` reads exit codes rather than reports, and a
pipeline's exit code is its LAST command's. Both mistakes shipped a red gate to
production, a week apart.
