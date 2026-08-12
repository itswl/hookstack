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
minutes ago, verdict matched, the P1 was not acted on", not a fresh start. The pipe stays
content-blind; the judge is untouched; failure still completes the loop (a
stopped or crashed investigation reports itself). The plain demo compose
points the escalation at the sink's `/probe-standin` so the shape is visible
without a model key; `--profile probe` (plus `HOOKPROBE_EVENT_URL` in `.env`)
swaps in the real investigator. First live run of the loop: a "host CPU high"
alert came in, the judge ruled it medium within a second, and 3.7 minutes
later the investigator's report landed on the same channels calling it a
false alarm — with the one actionable finding named.

## Parallel subagents

The engine's Task tool is enabled: a cascading incident can fan out into
parallel sub-investigations, each appearing in the process feed as a `Task`
event. Hooks apply inside subagents too, so the bash guard binds them the
same as the main loop.

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

## Web UI — operate sessions from a browser

`http://<host>:8088/ui` is a single self-contained page (no build step, no
external assets): sessions on the left, the conversation on the right, a
composer at the bottom. Paste the bearer token once (kept in localStorage).
From there you can read any investigation turn by turn (JSON reports
pretty-print, Markdown answers render, long alert payloads collapse), watch a
running turn's live process feed (tool calls, narration, the plan checklist),
**Stop** a runaway turn, send follow-ups into a finished session, or hit
**+ new session** for a free-form investigation. **skills** browses the
distilled runbooks; **memory** edits the environment memory (CLAUDE.md) that
every investigation starts from.

## Follow-up exploration — reuse the session

Every finished run keeps its engine session (transcripts live under
`$HOME/.claude` on the volume, so they survive restarts). Three ways in — the
web UI above, or:

```bash
# 1. HTTP: another turn in the same investigation, then poll /final again
curl -s -X POST localhost:8088/sessions/hook:deep-analysis:x:1/continue \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"message": "root_cause 说节点超卖 —— 查一下该节点的 allocatable 和邻居 Pod 的 requests 佐证"}'

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

The stock image ships **no** diagnostic CLIs — extend it with the read-only
tools your alerts actually need (see the commented `kubectl` example in the
Dockerfile), and hand MCP servers to the agent via `HOOKPROBE_MCP_CONFIG`.

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
