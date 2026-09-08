# hookprobe — running and operating it

Deployment, the sessions page, and how to keep asking after the first answer.
The sixty-second version is in the [README](../README.md); this is the rest.

## Run it

Four composes, four shapes: the repo-root stack compose runs the demo
stack and includes this service behind `--profile probe`; the repo-root
`deploy/docker-compose.yml` runs the real stack (pipe + brain +
investigator, no demo containers); `deploy/docker-compose.yml` here runs
the investigator standalone; `deploy/docker-compose.prod.yml` is the
production shape — joined to the docker network of the platform it serves,
admin port on loopback only, state bind-mounted at the deployment root for
backup and review.

**Pairing is the caller's job, and a compose file's rather than a command's.**
A caller reaches this service by name (`http://hookprobe:8088`), which means
docker DNS, which means both containers on one network. Two ways to arrange that,
and only one of them is right for a shared service:

The caller joins this service's network. It depends on the investigator already,
so the dependency runs the way it already runs, and this service keeps standing
alone — which is what lets it serve a second caller, or none:

```yaml
# in the CALLER's compose
services:
  its-worker:
    networks: [its-own-net, investigator]   # list its own again: naming any
networks:                                   # network opts out of the others
  investigator:
    name: hookstack_default                 # or whatever `docker network ls` says
    external: true
```

The other way round — this service joining the caller's network — is what
`deploy/docker-compose.prod.yml` does, and there it is correct: that file is one
installation's deployment, pinned to the platform it was written for. Do not put
it in the demo stack's compose or a local override. A service that cannot start
until its consumer is running has the dependency backwards.

Either way, declare it. `docker network connect` does the job once and survives a
restart but not a recreate, so the next `up --build` takes the leg down silently
and the caller only finds out when an analysis stops coming back.

Standalone, from the repo root:

```bash
printf 'HOOKPROBE_TOKEN=change-me\nANTHROPIC_API_KEY=sk-ant-...\n' > .env
docker compose --env-file .env \
  -f hookprobe/deploy/docker-compose.yml up -d --build
curl -s localhost:8088/healthz

# Smoke test one run end to end:
curl -s -X POST localhost:8088/hooks/agent \
  -H "Authorization: Bearer change-me" -H 'Content-Type: application/json' \
  -d '{"message": "Reply with exactly: {\"summary\": \"hookprobe smoke test ok\"}", "sessionKey": "smoke:1"}'
curl -s -H "Authorization: Bearer change-me" localhost:8088/sessions/smoke:1/final
```

The image ships a lean read-only diagnostic core (procps, jq, iproute2,
dnsutils, netcat, lsof — what any investigation reaches for first and what
marketplace runbooks assume exists); domain CLIs stay opt-in behind
commented Dockerfile blocks (`kubectl`, postgres/mysql/redis clients). Hand
MCP servers to the agent via `HOOKPROBE_MCP_CONFIG`.

Three more surfaces shape a run, all optional:

- **System prompt append** — drop operator methodology into
  `{workdir}/system-prompt.md` (or point `HOOKPROBE_SYSTEM_PROMPT_APPEND`
  at a file). It is appended to the engine's own system prompt and read
  fresh at every run, so edits apply without a restart.
  `examples/system-prompt.md` is a starting point, and exists because of a
  measurement: three subagent roles shipped, loaded into every run, and were
  invoked **zero** times across 260 recorded tool calls. The capability was
  provided and never instructed, so nothing used it. That file says when to
  delegate and — at more length — when not to.
- **Named subagent roles** — `.claude/agents/*.md` files load like skills
  (project and user layers both), or pin roles in deployment config with
  `HOOKPROBE_AGENTS_CONFIG` (JSON: name → {description, prompt, tools?,
  model?, skills?}). The main agent delegates to them through the Task
  tool; the bash guard binds them the same as the main loop. No roles ship
  by default — a measured week of production traffic never delegated once,
  so the seeded examples were removed (the decision and its evidence:
  `.agents/notes/implemented/2026-08-24-the-zero-delegations-were-a-broken-knob.md`).
  Add your own via `PUT /v1/agents/{name}` or the config above.
- **Audit trail** — every tool call in every run (subagents included)
  appends one JSONL line to `{workdir}/audit/YYYY-MM-DD.jsonl`: timestamp,
  session, tool, one-line detail, error flag. The run's event feed is the
  live view; this is the uncapped, greppable account across runs, pruned
  by the same retention window as case files.

## Web UI — operate sessions from a browser

`http://<host>:8088/ui` is a single self-contained page (no build step, no
external assets): sessions on the left, the conversation on the right, a
composer at the bottom. Paste the bearer token once (kept in localStorage).
From there you can read any investigation turn by turn (JSON reports
pretty-print, Markdown answers render, long alert payloads collapse), watch a
running turn's live process feed (tool calls, narration, the plan checklist),
**Stop** a runaway turn, send follow-ups into a finished session, or hit
**+ new session** for a free-form investigation. The sidebar filters by
key/title and flags relay-born sessions with their return outcome.

Six more views cover the rest of the surface: **skills** browses and edits
the runbooks (layer-tagged, copy-on-write); **agents** does the same for
subagent roles (config-pinned ones shown read-only); **memory** edits the
environment memory (CLAUDE.md); **prompt** edits the system-prompt append —
both hot-read by the next run; **system** shows the runtime knobs (secrets
as set/unset, never values), the MCP servers the next run would load, and
the health counters; **audit** follows the flight recorder, filterable by
session. A **help** view carries the whole manual — what this is,
the three-step start, every view, the API contract with curl templates,
the file map and the safety model — written for a new operator or an AI
driving the API, reachable at `#help`.

## Follow-up exploration — reuse the session

Every finished run keeps its engine session (transcripts live under
`$HOME/.claude` on the volume, so they survive restarts). Three ways in — the
web UI above, or:

```bash
# 1. HTTP: another turn in the same investigation, then poll /final again
curl -s -X POST localhost:8088/sessions/hook:deep-analysis:x:1/continue \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"message": "root_cause says the node is oversubscribed — check that node allocatable and the neighbour pods requests back that up"}'

# 2. Terminal: interactive REPL with the full investigation context
docker compose exec hookprobe sh -c 'cd /data && claude -r <engine_session_id>'
#   (engine_session_id comes from GET /v1/runs/{key})
```

Follow-ups run under the same guard posture and timeout clamps as first
passes. A failed follow-up never erases the original answer — earlier finals
are kept on the run record (`previous_texts`).

### From a chat thread: one topic, one run, one engine session

The same mechanism is what the chat uses, with the pipe and the bridge in
between ([deploy/lark-bridge/README.md](../../deploy/lark-bridge/README.md)).
The mapping is exactly this:

- **A new topic is a new run is a new engine session.** A top-level
  @-mention reaches the event door as `kind: brief` (or `task` on the work
  deployment) with `fields.thread_root`; `service.start` creates the run
  `probe:lark-thread:<event>`, the engine opens a fresh Claude Agent SDK
  session, and when the run finishes its `engine_session_id` and the topic's
  root are on the record. The first report is posted inside the topic.
- **A reply in the topic is another turn in that session.** The pipe resolves
  the reply's root to the run (through the report that carried the same
  `thread_root`) and the door calls `continue_run`, which spawns the engine
  with `resume=<engine_session_id>` — the first turn's tool output, evidence
  and dead ends are all still in context. That is why a follow-up costs cents
  and seconds where the first turn cost dollars and minutes: almost all of
  the prompt is read back from the cache instead of being sent and written
  again.
- **A reply under an alert card is the same thing** — the chain that card
  belongs to carries an investigation session, and the reply continues it.
- Everyone replying in one topic continues the same session and shares its
  context, provided each sender is in `HOOKPROBE_FOLLOW_UP_SENDERS`.

When a reply does **not** resume the session, on purpose, and says so in the
pipe's record:

| situation | what happens |
|---|---|
| the previous turn is still running | `busy` — nothing is queued or started; ask again when it has answered |
| the run crashed before the SDK reported a session id | `skipped`: "left no session to continue" — open a new topic |
| the run has answered 20 follow-ups already | `skipped` — a hard cap per run, so one thread cannot become an open-ended bill |
| the same message id arrives again (the platform redelivers) | `already_done` — one message, one turn |
| the engine transcript was pruned by `HOOKPROBE_RETENTION_DAYS` | `skipped`: "left no session to continue" — the run record survives, the context does not |
| the sender is not on the allowlist | `skipped` — before anything else is looked at |

Where the mapping lives: in the pipe's ledger (the platform message id of every
card it sent, and `fields.thread_root` on every report that answered into a
topic), never in the bridge. The bridge can be restarted or moved to another
app without a thread losing its session.

## The board — one row per piece of work

`/ui#work` groups runs into **work items**: the thing somebody wants finished,
which is usually one investigation and sometimes not. A re-fire and a chat
follow-up add turns to work that already exists; a plan handed to a work runner
is two runs on two services. Counting runs would report those as three or four
pieces of work, most of them phantom, so the board counts work.

The identity is `work_id`, decided by the event door: what an upstream node
stated (`fields.work_id` — how a handoff stitches a plan to the work it became),
else the correlation the pipe puts on every delivery, else the run's own session
key. Nothing reads content to decide it.

Five columns, blocked first, because a running item needs nothing from the person
reading the page:

| column | what it means |
|---|---|
| **waiting on you** | a procedure is proposed and nothing runs until somebody presses |
| **needs a human** | the last run failed — including a refusal for budget |
| **in flight** | a turn is running |
| **verifying** | a procedure ran and every step exited 0, and nobody has said the condition cleared |
| **done** | finished with nothing blocked |

Three numbers sit above it. `blocked` is the first two columns. `verified` counts
work a ruling called useful or whose own procedure came back clean — the only two
signals this node can check without a person. `closed without anyone stepping in`
is the strict one: done, verified, and it never had to stop and ask. On an
unattended deployment that number is often zero, and that is the true reading —
it says the verification loop is not closed, not that nothing happened.

`/ui#approvals` is the other half: every procedure waiting for approval, every
memory line an investigation proposed, and every report nobody has ruled on, with
the buttons. It answers "what do I owe", which is deliberately wider than "what is
blocked" — a memory line blocks nothing and still waits for a person.

`GET /v1/agent` says what this node is — name, role, runtime, policy, health — so
a deployment running an investigator, a planner and a work runner can tell them
apart by something other than a port. Set `HOOKPROBE_AGENT_NAME` and
`HOOKPROBE_AGENT_ROLE` per service.

## Parallel subagents

The engine's Task tool is enabled: a cascading incident can fan out into
parallel sub-investigations, each appearing in the process feed as a `Task`
event. Hooks apply inside subagents too, so the bash guard binds them the
same as the main loop.

## Development

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
bash scripts/gate.sh   # the exact CI list: compileall, ruff, page JS, pytest
```

Tests inject fake engines; nothing in the suite needs the SDK, an API key,
or the network.
