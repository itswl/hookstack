# hookprobe

A single-purpose deep-analysis agent runner. Third member of the hook\* family:

| project | role |
|---|---|
| hookrelay | the pipe — adapts alert dialects in and out, content-blind |
| hookjudge | the judge — one cheap verdict per alert |
| **hookprobe** | **the investigator — one read-only agent run per analysis task** |

hookprobe is what you run instead of a full OpenClaw gateway when all you
need from it is "take a task, run a tool-using agent, return the text". It
accepts one task over an OpenClaw-compatible HTTP contract — any client that
already integrates that gateway dialect as an analysis backend switches by
changing a URL — runs one unattended agent session (Claude Agent SDK:
built-in tools, MCP servers, web search, SKILL.md skills), and serves the
final report to whoever polls for it. No channels, no device pairing, no chat
history — and a run has a real terminal state, so the caller needs no
stability heuristics.

```
caller ── POST /hooks/agent ───────────────▶ hookprobe ── Claude Agent SDK
   ▲                                          │  bash guard (read-only)
   └───── GET /sessions/{key}/final ◀─────────┘  MCP / WebSearch / skills
            200 {isFinal: true, text}            /data: skills + results
```

## Contract

| route | behavior |
|---|---|
| `POST /hooks/agent` | Body: `{message, sessionKey, timeoutSeconds, ...}` (extra OpenClaw fields accepted and ignored). Starts a run, idempotent per `sessionKey`. Returns `{runId, sessionKey}`. |
| `POST /hooks/event` | The family's escalation door: hookrelay's `to-probe` channel delivers normalized events here (`X-Hook-Signature` timestamped HMAC when `HOOKPROBE_EVENT_SECRET` is set). Levels outside `HOOKPROBE_ESCALATE_LEVELS` are acknowledged and skipped; the rest start an investigation, idempotent per `(source, event_id)` — a restatement storm funds one investigation, not N. |
| `GET /sessions/{key}/final` | `202` while running · `200 {"isFinal": true, "text", "messageCount"}` when done · `404` unknown (e.g. in-flight run lost to a restart). `isFinal` is always true on a 200. |
| `POST /sessions/{key}/continue` | Body: `{message, timeoutSeconds?}`. Follow-up turn in the **same** engine session — full investigation context retained. `409` while a turn is in flight, `404` unknown. Poll `/final` again for the new answer. |
| `POST /sessions/{key}/stop` | Cancel the in-flight turn; it settles as a failed turn ("stopped by operator") within one poll. `409` when nothing is running. |
| `GET/PUT /v1/memory` | The environment memory — `{workdir}/CLAUDE.md`, loaded into every engine session. Facts every investigation should start from: topology, known false alarms, conventions. |
| `GET /v1/runs` | Session list, newest first (summaries). |
| `GET /v1/runs/{key}` | Full run record: status, error, cost, engine session id, all turns. |
| `GET /ui` | The sessions page (below). Markup is served unauthenticated; the data calls it makes are not. |
| `GET /healthz` | Liveness, unauthenticated. |

Auth: `Authorization: Bearer $HOOKPROBE_TOKEN` on everything except `/healthz`.

A run that fails (crash, timeout, empty output) still finishes the contract:
`isFinal: true` with a well-formed report whose `root_cause` names the runner
failure — the operator sees the error on the analysis card within one poll
instead of waiting out the caller's timeout window.

## Adopting it from an OpenClaw integration

A client that already triggers analyses through an OpenClaw gateway needs no
code change — point its gateway URL and hooks token here:

```bash
# wherever your client configures its OpenClaw endpoints:
GATEWAY_URL=http://hookprobe:8088     # trigger: {url}/hooks/agent
HTTP_API_URL=http://hookprobe:8088    # poll:    {url}/sessions/{key}/final
HOOKS_TOKEN=<same value as HOOKPROBE_TOKEN>
```

Because `/final` answers with `isFinal: true`, a poller can write the result
on the first confirming read — any stability heuristics built against a
moving answer simply never trigger. hookprobe is prompt-agnostic: the
analysis prompt stays on the caller's side, and whatever `message` arrives is
what runs.

## Configuration

| env | default | meaning |
|---|---|---|
| `HOOKPROBE_TOKEN` | *(empty = unauthenticated)* | Bearer token callers must present |
| `HOOKPROBE_MODEL` | `claude-opus-5` | Model for the agent session |
| `HOOKPROBE_MAX_TURNS` | `32` | Hard agent-loop budget per run |
| `HOOKPROBE_MAX_CONCURRENT` | `2` | Parallel runs; the rest queue |
| `HOOKPROBE_DEFAULT_TIMEOUT_SECONDS` | `900` | When the trigger omits `timeoutSeconds` |
| `HOOKPROBE_MAX_TIMEOUT_SECONDS` | `1800` | Upper clamp on requested timeouts |
| `HOOKPROBE_WORKDIR` | `/data` | Persistent workspace (skills, results) |
| `HOOKPROBE_MCP_CONFIG` | *(unset)* | Path to an `.mcp.json`-shaped file of MCP servers |
| `HOOKPROBE_EVENT_SECRET` | *(empty = unsigned)* | Verifies the pipe's deliveries to `/hooks/event` |
| `HOOKPROBE_RETURN_URL` | *(unset = no return)* | Where event-door investigations report back — the pipe's `probe-notify` front door |
| `HOOKPROBE_RETURN_SECRET` | *(empty = unsigned)* | Signs the return delivery (timestamped HMAC) |
| `HOOKPROBE_ESCALATE_LEVELS` | `critical,high` | The only content judgement the investigator makes: which levels are worth a paid run |
| `HOOKPROBE_BUDGET_USD` | `0` *(off)* | Window spend ceiling for the event door; refusals report themselves. `GET /v1/budget` shows the arithmetic |
| `HOOKPROBE_BUDGET_WINDOW_HOURS` | `24` | The sliding window the budget is measured over |
| `HOOKPROBE_RETENTION_DAYS` | `0` *(keep all)* | Case files and transcripts older than this are pruned daily; skills and memory are never touched |
| `HOOKPROBE_HOST` / `HOOKPROBE_PORT` | `0.0.0.0` / `8088` | Bind address |
| `ANTHROPIC_API_KEY` | — | Or `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN` for a relay |

## Security model

Read-only is enforced in three layers, strongest first:

1. **Credentials** — mount query-only credentials (read-only kubeconfig,
   Prometheus/Loki endpoints, viewer tokens). This is the real boundary;
   treat the other two as convenience.
2. **Bash guard** — a PreToolUse hook denies mutating verbs of kubectl, helm,
   docker/podman, systemctl, terraform, plus ssh/scp and `git push`. It errs
   toward over-blocking. HTTP verbs are not policed (query APIs POST), and
   cloud CLIs are too many to enumerate — scope their credentials instead.
3. **Container** — non-root, disposable, nothing precious inside. The agent
   may write freely in `/data` (scratch + skills); that is by design.

## The family loop

Inside hookstack the investigator is wired into the alert flow itself: the
pipe's escalation routes copy every front-door event to `/hooks/event`, the
probe decides by level whether an investigation is worth paying for, and the
finished report POSTs back to the pipe's `probe-notify` door — dressed by the
pipe and delivered to the same channels as the verdict. Escalated
investigations also open the case files first: the task brief tells the agent
to grep `/data/results/` for earlier investigations of the same alert and
report how the last verdict held up — a recurrence gets "first seen 101
minutes ago, verdict matched, the P1 was not acted on", not a fresh start.

The event door is also where the budget breaker lives, because it is the one
path that spends money without a human asking. Set `HOOKPROBE_BUDGET_USD`
(with `HOOKPROBE_BUDGET_WINDOW_HOURS`, default 24): once the window's
recorded spend reaches the budget, new escalations are refused — but a
refusal is not a silent drop. It settles as a report-shaped run and returns
through the same loop, so the channels say *why* there is no investigation
("Budget breaker open…"). Idempotency still holds (a redelivered, already-funded event
is never refused), operator paths — `/hooks/agent`, follow-ups, the UI — are
never gated, and `GET /v1/budget` shows the window's arithmetic. The figure
counts recorded turns only, so in-flight runs can overshoot by at most
`max_concurrent` investigations: it is a brake, not an invoice. The pipe stays
content-blind; the judge is untouched; failure still completes the loop (a
stopped, crashed, budget-refused — or restart-orphaned — investigation
reports itself: runs are checkpointed at spawn, and the next boot sweeps
whatever a dead process left mid-flight into failure reports). The plain demo compose
points the escalation at the sink's `/probe-standin` so the shape is visible
without a model key; `--profile probe` (plus `HOOKPROBE_EVENT_URL` in `.env`)
swaps in the real investigator. First live run of the loop: a "host CPU high"
alert came in, the judge ruled it medium within a second, and 3.7 minutes
later the investigator's report landed on the same channels calling it a
false alarm — with the one actionable finding named.

## Patrol mode — proactive investigations, zero new code

The family loop is event-driven, but nothing says the event has to come
from a monitor. A cron job that posts a "patrol due" event to the pipe's
front door gets the whole machinery for free: escalation copies it to the
investigator, the investigation runs with every skill/role/MCP it has, and
the report lands on the same channels as any alert:

```cron
# host crontab: a daily 09:00 patrol through the pipe's front door
0 9 * * * curl -sf -X POST http://127.0.0.1:8100/hook/inbound \
  -H 'content-type: application/json' \
  -d '{"title":"Daily patrol","message":"Run a read-only health patrol of the infrastructure: host resources, key ports, the family services own checks. List anything abnormal in priority order; if all is well, say what you checked","state":"alerting","env":"prod"}'
```

Each firing is a new event id, so each patrol is its own investigation with
its own case file — and the case-file recall means patrol N cites what
patrol N-1 found. The budget breaker applies as usual: a patrol is an
autonomous spend like any other.

## Parallel subagents

The engine's Task tool is enabled: a cascading incident can fan out into
parallel sub-investigations, each appearing in the process feed as a `Task`
event. Hooks apply inside subagents too, so the bash guard binds them the
same as the main loop.

## Managing MCP servers by hand

MCP is managed as one JSON file — no API writes, on purpose: server specs
carry credentials in their `env`, and secrets belong in a file you mount,
not in a web form. The loop is:

1. Write the file. Three dialects are accepted: the bare
   `{name: {command, args, env}}` mapping, the `.mcp.json` wrapper
   (`{"mcpServers": {...}}`), and the marketplace `config.json` shape —
   entries with `"enabled": false` are skipped and the flag is stripped, so
   downloaded MCP packages work unedited.
2. Mount it read-only and point `HOOKPROBE_MCP_CONFIG` at it (see the
   commented lines in every compose). A path on the volume (e.g.
   `/data/mcp.json`) also works and is editable without remounting.
3. Verify with `GET /v1/mcp`: it reads the file fresh and shows each
   server's command/args/type/url plus its env **key names only** — env
   values never leave the file.
4. Edit any time: the config is read fresh at every run (and every
   `/v1/mcp` call), so changes apply to the next investigation without a
   restart.

## Browser evidence (optional)

Give the agent an interactive browser for dashboards that have no API: copy
`deploy/mcp.example.json` (a headless Playwright MCP server), point
`HOOKPROBE_MCP_CONFIG` at it, and uncomment the chromium block in the
Dockerfile so the image ships the browser. One caution: a browser can click
and submit on any page it can reach — the bash guard does not see browser
actions, so point it at read-only dashboards and viewer accounts; `--isolated`
keeps it from retaining any profile state between runs.

## Skills — the runner gets smarter

The deep-analysis prompt asks the agent to distill verified diagnostic paths
into reusable SKILL.md runbooks. Those land in `/data/.claude/skills` on the
persistent volume and are loaded into every later run. Back up the volume if
you care about the accumulated experience. The skills directory is plain
files — review it, prune bad runbooks, or `git init` it for history; anything
written there instructs future runs, so treat it as part of your trust
boundary.

Skills load in two layers. The **project layer** (`{workdir}/.claude/skills`,
on the volume) is always on — it is where the agent distills. The **user
layer** is an optional host library: mount it read-only at
`/data/home/.claude/skills` (only the skills subdir — never the whole
`~/.claude`, credentials live there) and set
`HOOKPROBE_SETTING_SOURCES=user,project`. A host library tends to be big, so
`HOOKPROBE_SKILLS` pins the session's skill list to named skills (or `all`);
it is a context filter, not a sandbox. The `/v1/skills` browser shows
exactly the layers the engine would load, tagged `project`/`user`. Two
honest caveats. A skill is instructions, not a runtime — host skills that
shell out to binaries the image does not carry will load and then fail at
the tool, so pin `HOOKPROBE_SKILLS` to the ones whose tools exist. And mount
the RESOLVED directory: skill libraries are often symlink farms
(`~/.claude/skills/x -> ../../.agents/skills/x`), and a bind mount carries
the links but not their targets — `readlink` one entry first and mount what
it points at.

The format is not ours and that is the point: a skill is a directory with a
`SKILL.md` (YAML frontmatter: `name`, `description`), the shape the whole
OpenClaw-lineage ecosystem shares. Marketplace packages install unchanged —
verified live with two from the OpenOcta market
(`https://openocta.com/api/v1/skills`, ~750 skills, strong ops section):
unzip into `/data/.claude/skills/<name>/` (strip `__MACOSX`), the next run
loads them, and the engine invoked `server-patrol` by name and followed its
runbook. The trust boundary above applies double to third-party skills:
read them before installing — they will be instructing an agent that holds
your read-only credentials.

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

Follow-ups run under the same read-only guard and timeout clamps as first
passes. A failed follow-up never erases the original answer — earlier finals
are kept on the run record (`previous_texts`).

## Run it

Four composes, four shapes: the repo-root stack compose runs the demo
family and includes this service behind `--profile probe`; the repo-root
`deploy/docker-compose.yml` runs the real family (pipe + brain +
investigator, no demo containers); `deploy/docker-compose.yml` here runs
the investigator standalone; `deploy/docker-compose.prod.yml` is the
production shape — joined to the docker network of the platform it serves,
admin port on loopback only, state bind-mounted at the deployment root for
backup and review.

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
- **Named subagent roles** — `.claude/agents/*.md` files load like skills
  (project and user layers both), or pin roles in deployment config with
  `HOOKPROBE_AGENTS_CONFIG` (JSON: name → {description, prompt, tools?,
  model?, skills?}). The main agent delegates to them through the Task
  tool; the bash guard binds them the same as the main loop. A fresh
  volume is seeded once with three readable examples (log-analyst,
  metrics-analyst, net-diagnostician) so the format teaches itself —
  edit or delete them freely, the `.defaults-seeded` marker keeps reboots
  from re-writing your choices.
- **Audit trail** — every tool call in every run (subagents included)
  appends one JSONL line to `{workdir}/audit/YYYY-MM-DD.jsonl`: timestamp,
  session, tool, one-line detail, error flag. The run's event feed is the
  live view; this is the uncapped, greppable account across runs, pruned
  by the same retention window as case files.

## Development

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
bash scripts/gate.sh   # the exact CI list: compileall, ruff, page JS, pytest
```

Tests inject fake engines; nothing in the suite needs the SDK, an API key,
or the network.

## Non-goals (v1)

- OpenClaw-dialect triggers poll; there is no `callbackUrl` on that contract
  yet. (Family event-door runs do report back — see The family loop.)
- In-flight runs do not survive a restart (finished results do). The caller's
  retry path covers this.
- No queue durability beyond the process: this runner is one container behind
  one orchestrator, not a job system.
