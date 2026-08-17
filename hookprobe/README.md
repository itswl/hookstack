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
| `POST /hooks/event` | The family's escalation door: hookrelay's `to-probe` channel delivers normalized events here (`X-Hook-Signature` timestamped HMAC when `HOOKPROBE_EVENT_SECRET` is set). Levels outside `HOOKPROBE_ESCALATE_LEVELS` are acknowledged and skipped; the rest start an investigation, idempotent per `(source, event_id)` — redelivery of one event funds one investigation, not N. A restatement that arrives as a NEW event id is a new investigation (the judge's `reuse` has no equivalent here); the budget breaker is the backstop for that. |
| `GET /sessions/{key}/final` | `202` while running · `200 {"isFinal": true, "text", "messageCount"}` when done · `404` unknown (e.g. in-flight run lost to a restart). `isFinal` is always true on a 200. |
| `POST /sessions/{key}/continue` | Body: `{message, timeoutSeconds?}`. Follow-up turn in the **same** engine session — full investigation context retained. `409` while a turn is in flight, `404` unknown. Poll `/final` again for the new answer. |
| `POST /sessions/{key}/stop` | Cancel the in-flight turn; it settles as a failed turn ("stopped by operator") within one poll. `409` when nothing is running. |
| `GET/PUT /v1/memory` | The environment memory — `{workdir}/CLAUDE.md`, loaded into every engine session. Facts every investigation should start from: topology, known false alarms, conventions. |
| `GET /v1/runs` | Session list, newest first (summaries). |
| `GET /v1/runs/{key}` | Full run record: status, error, cost, engine session id, all turns. `inputs_now` carries the digests of the memory and methodology files *as they stand today*, so a recorded digest can be read as "still current" or "edited since". |
| `POST /v1/runs/{key}/distill` | A SKILL.md draft for what a finished run learned — the question, the tool sequence, the conclusion. Returns it; never writes it. Saving is still `PUT /v1/skills/{name}`, because an investigator that edits its own future instructions is one nobody reviewed. |
| `GET /v1/runs/{key}/stream` | The open run as it happens — NDJSON, one object per line: an opening `snapshot`, the answer arriving as `delta` chunks (`kind: text` or `thinking`), each finished step, a `ping` every 15s of silence, and `done` when it settles, at which point it closes itself. Deltas are live-only and never recorded; the finished blocks are what the case file keeps. |
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
| `HOOKPROBE_AUTO_DISTILL_MAX` | `0` *(manual)* | How many runbooks finished runs may leave behind. Above 0, each completed run writes its own `.claude/skills/<name>/SKILL.md` — from the service, create-only, marked unreviewed. See *The learning loop* |
| `HOOKPROBE_REPEAT_REMINDER_AT` | `3` *(0 = off)* | After N identical tool calls, remind the agent to change approach |
| `HOOKPROBE_BASH_TIMEOUT_MS` | `120000` *(0 = CLI default)* | Deadline for a single command (`BASH_DEFAULT_TIMEOUT_MS`) |
| `HOOKPROBE_BASH_MAX_TIMEOUT_MS` | `600000` *(0 = CLI default)* | Ceiling the agent may request per command (`BASH_MAX_TIMEOUT_MS`) |
| `CLAUDE_CODE_ENABLE_TELEMETRY` + `OTEL_*` | *(unset = off)* | Passed to the CLI, which emits one OpenTelemetry event per model call. No default endpoint — see below |
| `HOOKPROBE_HOST` / `HOOKPROBE_PORT` | `0.0.0.0` / `8088` | Bind address |
| `ANTHROPIC_API_KEY` | — | Or `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN` for a relay |

## Security model

Read-only is enforced in four layers, strongest first. The first three are
about the systems under investigation; the fourth is about the investigator
itself, which turns out to be the one target it can always reach.

1. **Credentials** — mount query-only credentials (read-only kubeconfig,
   Prometheus/Loki endpoints, viewer tokens). This is the real boundary;
   treat the other two as convenience. It is also the *first* thing to get
   right: an investigator with no credentials mounted can only reason about the
   alert payload, and no amount of tooling in the image changes that. Domain
   CLIs are build args, so adding one is a flag rather than an edit:
   `--build-arg APT_EXTRAS="postgresql-client redis-tools"`,
   `--build-arg KUBECTL_VERSION=v1.31.4`.
2. **Bash guard** — a PreToolUse hook denies mutating verbs of kubectl, helm,
   docker/podman, systemctl, terraform, plus ssh/scp and `git push`. It errs
   toward over-blocking. HTTP verbs are not policed (query APIs POST), and
   cloud CLIs are too many to enumerate — scope their credentials instead.
3. **Container** — non-root, disposable, nothing precious inside, no container
   runtime and no socket (a run cannot start a container, and cannot build a
   namespace of its own either). The agent may write freely in `/data`, minus
   the carve-out below.
4. **Input guard** — the agent may not write what steers the *next* run:
   `.claude/` (skills, roles, settings), `CLAUDE.md`, `system-prompt.md`, and
   the `audit/` flight recorder. Without this, one injected line reaching
   `.claude/skills/` outlives the run that read it and comes back as the
   operator's own runbook — a single injection made durable. Two mechanisms,
   because they fail differently: a PreToolUse hook refuses the write and says
   why, and a digest of every input file is compared before and after each run,
   which catches a change however it arrived. A run that changed its own inputs
   says so at the top of its record, unfolded.

   This is code rather than a read-only mount because the agent and the service
   share a UID and a process tree: a mount cannot tell `PUT /v1/skills/{name}`
   (an operator installing a reviewed runbook) from a run editing itself, and
   mounting these paths read-only would take the operator's write endpoints
   down along with the attack.

## The learning loop

The investigator is told to read prior case files, and the skills directory is
described as what earlier runs distilled — so the loop is only worth anything
if something writes one. `HOOKPROBE_AUTO_DISTILL_MAX` closes it: above 0, each
completed run assembles a runbook from its own record (the question, the tool
sequence in order, the conclusion) and installs it.

The write happens **in the service**, never through the agent's tools, and that
distinction is the whole design. Layer 4 above blocks the agent from writing
`.claude/` because a run that edits its own future instructions turns one
injected line into a permanent one. Automatic distillation is a different act
with a different failure mode, and the terms are what make it safe:

- **Create-only.** Replacing a runbook stays an operator action, so a bad run
  cannot overwrite a good one and an injection cannot rewrite what was
  approved. A recurring alert reuses its runbook rather than stacking copies.
- **Never from a run that failed, produced nothing, or changed its own
  inputs.** A run that already misbehaved does not get to leave instructions.
- **Capped**, because each runbook is prefix cost on every later run and no
  reviewer is deciding when to stop. At the cap the loop stops writing; it
  never evicts, because something there may have been reviewed and this is not
  the code that gets to price that.
- **Stamped.** `origin.json` beside the manifest records which run wrote it,
  when, on which model, and `reviewed: false`. The skills page shows those as
  `unreviewed`; the runbook's own text says so too, because the next
  investigation reads the text and cannot ask where it came from.

Every run records what the loop did — `{"installed": name}` or
`{"skipped": reason}` — so "it quietly did nothing again" is not a state this
can be in.

## Loop hygiene — the run's context and bill

Two advisory guards, neither a security boundary. They exist because an
unattended run cannot notice it is wasting itself.

**Repeat reminder.** Running an identical call again returns the same answer at
the same price. On the `HOOKPROBE_REPEAT_REMINDER_AT`-th identical call (and
every multiple after) the result carries a note telling the agent to change
approach or record what stays unknown and move on. The budget breaker stops
spending after the fact; this is the nudge before it costs.

**Per-command deadlines.** `HOOKPROBE_BASH_TIMEOUT_MS` and
`HOOKPROBE_BASH_MAX_TIMEOUT_MS` become the CLI's `BASH_DEFAULT_TIMEOUT_MS` /
`BASH_MAX_TIMEOUT_MS`, so a `curl` at an unreachable host or a `kubectl` at a
wedged API server cannot hold a run slot until the whole run times out. The
defaults match the CLI's own — this is a lever to tighten, not a change of
behaviour.

Every run also records **what the model was actually given**: model, skill
layers and their names, subagent roles, MCP servers, and content digests of the
environment memory and the appended methodology. Those files live on a mutable
volume, so without that record a report cannot be explained after the volume
moves on — a stale line in `CLAUDE.md` once made every report come back in the
wrong language while the request looked identical. It sits on each run and each
turn (`inputs`), and the console's session view shows it.

Both guards and the inputs record are borrowed from
[DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)
(`packages/guard`, and its "model-visible means logged" rule). Its third idea,
spilling oversized tool output to a file, was built here and then removed: the
Claude Code harness already does exactly that, and a second layer on top wrote a
file that only held the harness's already-truncated copy while claiming to hold
everything — see
[the rejected note](../.agents/notes/rejected/2026-08-14-tool-output-spill-in-hookprobe.md).

## What a run costs, and why reuse is the lever

Each turn shows what it cost: model, tokens in and out, cache reads, dollars,
seconds. The session's running total sits beside the session key, and the header
chip carries the window's spend and its **cache %** — how much context was reused
rather than paid for again.

That number matters more than it looks, because the part of the bill you cannot
negotiate is large. An investigation carries roughly 29k input tokens before its
alert is even mentioned, and measured on a running container it does not shrink:

| configuration | input tokens |
| --- | --- |
| preset `claude_code` + 12 tools | 26,920 |
| no preset + 12 tools | 25,569 |
| no preset + 4 tools | 25,569 |

The preset is worth ~1,350 tokens and cutting the tool list changes nothing —
`allowed_tools` gates execution, not what is sent. Everything of ours (memory,
appended methodology, skills, roles) came to under 3% of the prefix on the
deployment this was measured on.

So the difference between an expensive week and a cheap one is reuse, and three
habits decide it:

- **Follow up in the session rather than opening a new investigation.** Measured
  pair on the same context: $0.1490 fresh, $0.0156 on the follow-up.
- **Let bursts run together.** Concurrent runs share a warm prefix, so lowering
  `HOOKPROBE_MAX_CONCURRENT` to save money does the opposite.
- **Batch edits to memory, prompt and skills.** Each change starts the prefix
  over; adjusting them between alerts pays the full entry fee every time.

Whether to pay that entry fee at all is decided upstream — `HOOKPROBE_ESCALATE_LEVELS`,
the event door's idempotency, and the budget breaker. Caching only sets the
discount. The reasoning and the rejected alternative (a timer to keep the cache
warm — it costs about ten times what it saves at this volume) are in
[.agents/notes](../.agents/README.md).

## Model-call telemetry (on by default, no backend assumed)

Every run's totals are already on its record — cost, tokens, per-model
breakdown, duration, and the tool steps. What that cannot show is the *shape* of
a run: how many model calls it took, which one was slow, where the context went,
that one investigation spent on two different models.

The bundled CLI emits all of that itself over OpenTelemetry, and the SDK merges
this container's environment into the CLI's, so it is configuration rather than
code. It is **on by default** because it costs nothing when nobody is listening —
measured, not assumed: with no endpoint set, a run finishes in the same time,
retries nothing and logs nothing. The only decision left is where to send it:

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://your-collector:4317   # that is the whole setup
```

No default endpoint, no vendor. Point it at whatever you already run, whenever
you get to it.

One event per model call, `claude_code.api_request`:

```
model: "deepseek-v4-pro"      input_tokens: 25853     output_tokens: 2
cost_usd: 0.129315            duration_ms: 1752       cache_read_tokens: 0
session.id: 1a09123d…         prompt.id: bcf7d8b9…    event.sequence: 1
query_source: "sdk"           effort: "high"
```

Alongside it: `assistant_response`, `tool_decision`, and the counters
`claude_code.token.usage` / `cost.usage` / `tool.execution` / `subagent.spawn` /
`mcp.rpc` / `compaction` / `active_time.total`.

This is where per-model detail appears that a run record cannot show: one
investigation was observed spending on **two** models — a fast one for cheap
turns and the main one for the reasoning — while the run's own total is a single
number. Anyone re-pricing usage needs both names.

### What is redacted, and by whom

The CLI substitutes `<REDACTED>` for content in these events — nothing in this
repository does that — under five independent switches. Measured on 2.1.229:

| switch | exposes | default here |
| --- | --- | --- |
| `OTEL_LOG_ASSISTANT_RESPONSES` | the model's answers (`assistant_response.response`) | **on** |
| `OTEL_LOG_USER_PROMPTS` | the prompt — *and* the answers with it | **on** |
| `OTEL_LOG_TOOL_DETAILS` | tool arguments | **on** |
| `OTEL_LOG_TOOL_CONTENT` | whole tool outputs | **on** |
| `OTEL_LOG_RAW_API_BODIES` | raw request/response bodies per call | off |
| `CLAUDE_CODE_OTEL_CONTENT_MAX_LENGTH` | lifts the 60 KiB cap on those raw bodies | off |

Four of the five are on, which is a deliberate posture and not a default to drift
into: everything the agent was given and everything it produced leaves the
container in cleartext once an endpoint is set — including whatever a tool
happened to print, credentials included. The fifth is off because it is the one
that pays most and delivers least (below). It buys the material that cost and token
counts cannot give you: what the model was actually asked, and what it actually
answered. Note the asymmetry between the first two: enabling prompts also enables
answers, but not the reverse.

**The raw bodies are capped at 60 KiB.** The CLI truncates the attribute and says
so in the value (`[TRUNCATED - Content exceeds 60KB limit]`) while `body_length`
keeps reporting the true size, so the loss is visible rather than silent. Every
real investigation exceeds it — one call here carried 113 KB. Because a request
body is `{model, messages, system, tools}`, what falls off the end is the tool
schemas first and the conversation only after that.
`CLAUDE_CODE_OTEL_CONTENT_MAX_LENGTH` lifts the cap (measured: 113426 declared,
113425 received, valid JSON through the closing brace) and is left unset here —
uncapping on top of five open switches would push the whole conversation on every
call unconditionally. Set it when you want byte-exact replay. Note the unprefixed
`OTEL_CONTENT_MAX_LENGTH` also exists and does *not* affect these bodies.

Two things to know before you build on it, both measured rather than assumed
(CLI 2.1.229):

- **These are events, not spans.** `OTEL_TRACES_EXPORTER` is accepted but no
  spans were emitted for a plain run, and the events carry no `traceId`. A call
  tree is reconstructable from `session.id` + `prompt.id` + `event.sequence`; it
  does not arrive as one.
- **`cost_usd` prices Claude models.** On another provider reached through the
  Anthropic dialect it is an over-estimate — the run above reports $0.129 for a
  DeepSeek call. The event carries the real model name and the raw token counts,
  so re-price from those and treat the field as a relative signal.

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
it in the demo family's compose or a local override. A service that cannot start
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
