# hookprobe

One unattended agent run, behind an HTTP contract that terminates.

You POST a task. hookprobe runs a single tool-using agent session (Claude Agent
SDK: bash, MCP servers, web search, SKILL.md skills) and serves the report to
whoever polls for it. No channels, no device pairing, no chat history — and a
run reaches a real terminal state, so the caller needs no stability heuristics
to decide the answer stopped moving.

It speaks an OpenClaw-compatible dialect, so a client that already treats that
gateway as its analysis backend switches by changing a URL.

## Sixty seconds

```bash
git clone https://github.com/itswl/hookstack && cd hookstack
printf 'HOOKPROBE_TOKEN=change-me\nANTHROPIC_API_KEY=sk-ant-...\n' > .env
docker compose --env-file .env -f hookprobe/deploy/docker-compose.yml up -d --build

curl -s -X POST localhost:8088/hooks/agent \
  -H "Authorization: Bearer change-me" -H 'Content-Type: application/json' \
  -d '{"message":"Which processes are listening, and on what?","sessionKey":"demo:1"}'

curl -s -H "Authorization: Bearer change-me" localhost:8088/sessions/demo:1/final
# 202 while it works · 200 {"isFinal": true, "text": ...} when it is done
```

Then `http://127.0.0.1:8088/ui` for the sessions page.

## Why this one

**It terminates.** `/final` answers `202`, or a `200` that is final — including
when the run crashed or timed out, where the report's `root_cause` names the
runner failure instead of leaving the caller to wait out its own timeout. A
poller can write the result on its first confirming read.

**The agent cannot edit what steers the next run.** `.claude/` (skills, roles,
settings), `CLAUDE.md` and the audit log are closed to it — by a PreToolUse hook
that refuses the write, and by a digest of every input file compared before and
after each run, because the two fail differently. Without this, one injected
line reaching `.claude/skills/` outlives the run that read it and comes back as
the operator's own runbook. Read-only is enforced in four layers; this is the
one about the investigator itself, which turns out to be the only target it can
always reach. See [Security model](#security-model).

**Finished runs leave runbooks behind.** A completed investigation distills its
own record — the question, the tool sequence, the conclusion — into a
`SKILL.md`, written by the service and never through the agent's tools. The
second investigation of the same condition adds a case rather than replacing
what was there, and every write, by a run or by a person, snapshots what it
displaced first. See [docs/learning.md](docs/learning.md).

## The hook\* family

hookprobe stands alone. It is also the third member of a family that splits one
job three ways — carrying a signal, judging it, and investigating what earns it:

| project | role |
|---|---|
| hookrelay | the pipe — adapts every upstream dialect in and every downstream format out, content-blind |
| hookjudge | the judge — one cheap verdict per signal |
| **hookprobe** | **the investigator — one read-only agent run per analysis task** |

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
| `POST /hooks/event` | The family's escalation door: hookrelay's `to-probe` channel delivers normalized events here (`X-Hook-Signature` timestamped HMAC when `HOOKPROBE_EVENT_SECRET` is set). Levels outside `HOOKPROBE_ESCALATE_LEVELS` are acknowledged and skipped; the rest start an investigation, idempotent per `(source, event_id)` — redelivery of one event funds one investigation, not N. A restatement that arrives as a NEW event id is a new investigation (the judge's `reuse` has no equivalent here); the budget breaker is the backstop for that. **`fields.kind` picks the question:** `brief` means the body IS the instruction — a scheduled procedure with its own steps and its own output contract, run as written rather than wrapped in an analysis frame (and given a 16 KB body cap rather than 4 KB, because truncating a procedure deletes STEPS rather than detail). It is not a trust boundary: the wrapper never sanitised anything, and the door's HMAC is still the whole of what decides who may speak. `task` asks how the work would be done — what exists now, what is missing, the steps, how the result would be verified, and what could not be seen NAMED rather than guessed at; anything else, including absent, asks what broke. The caller says which because the pipe stays content-blind, and a work item handed the root-cause prompt gets an incident report about something that is not broken. Neither question proposes anything for execution on this door — `task` deliberately emits no remediation block at all. |
| `POST /hooks/action` | The card's way back in, signed like the door above. Body: `{action: {kind, params}, correlation_id, event_id, actor, at}`. `kind` is one of `followup` (resume the investigation with `params.prompt`, or a default question), `approve` (`params.ref` names a remediation proposal), `useful` / `useless` (a human's ruling on the report). `202` with what it did, **including** what it refused — a denial is a sentence somebody reads in a chat, and an HTTP error on an IM callback becomes a retry loop. `401` unsigned, `400` malformed, `404` naming a session or proposal that does not exist. Idempotent per `(correlation_id, kind, at)`: this door starts paid turns and runs commands, so a redelivery reads back the first press's answer instead of buying a second turn. |
| `GET /sessions/{key}/final` | `202` while running · `200 {"isFinal": true, "text", "messageCount"}` when done · `404` unknown (e.g. in-flight run lost to a restart). `isFinal` is always true on a 200. |
| `POST /sessions/{key}/continue` | Body: `{message, timeoutSeconds?}`. Follow-up turn in the **same** engine session — full investigation context retained. `409` while a turn is in flight, `404` unknown. Poll `/final` again for the new answer. |
| `GET /v1/automation` | Each class of automation, its declared ceiling, its record of proposals and the human decisions on them, and the tier the record WOULD support. Counters, never agreement rates. The page an operator reads before deciding a class has earned a step up. |
| `POST /v1/automation/{class}/{id}/regret` | A sampling review saying an auto-applied action was wrong — the one event that resets a class's argument for its tier, and how an unattended deployment gets its human-in-the-loop back asynchronously. Operator token only; no run can label its own work. |
| `POST /v1/runs/{key}/handoff` | The one human step in a chain that is otherwise automatic: post this finished run's report to `HOOKPROBE_HANDOFF_URL`, signed. Off unless that URL is set (`501`), refused for a run with nothing to say (`409`). It posts to a **pipe door**, never to another node — one hop shorter would leave no event, no trace, no correlation and no dedup, and dedup is how a second click becomes a `duplicate` in the ledger instead of a second paid run. The agent cannot reach this route: its subprocess does not inherit the service's secrets, so a curl from inside a run has no token to present. |
| `POST /sessions/{key}/stop` | Cancel the in-flight turn; it settles as a failed turn ("stopped by operator") within one poll. `409` when nothing is running. |
| `GET/PUT /v1/memory` | The environment memory — `{workdir}/CLAUDE.md`, loaded into every engine session. Facts every investigation should start from: topology, known false alarms, conventions. |
| `GET /v1/runs` | Session list, newest first (summaries). |
| `GET /v1/runs/{key}` | Full run record: status, error, cost, engine session id, all turns. `inputs_now` carries the digests of the memory and methodology files *as they stand today*, so a recorded digest can be read as "still current" or "edited since". |
| `POST /v1/runs/{key}/distill` | A SKILL.md draft for what a finished run learned — the question, the tool sequence, the conclusion. Returns it; never writes it. Saving is still `PUT /v1/skills/{name}`; the automatic path is `HOOKPROBE_AUTO_DISTILL_MAX` (see [docs/learning.md](docs/learning.md)). |
| `GET /v1/skills/export` | Runbooks packaged to LEAVE this deployment. Cuts at `distill.CASES_MARKER`, so the procedure a consolidation wrote travels and the CASES it was distilled from — where the session keys, hostnames and figures live — do not. Credentials are redacted; anything else identifying is REPORTED under `review`, never removed, and `must_read` is emitted whether or not a pattern matched, because `review: []` means *no known shape matched* and never *safe to publish*. Only consolidated runbooks are exportable: an auto-written one is a pile of cases, and cutting them out leaves a title. |
| `POST /v1/skills/{name}/review` | Mark a runbook read without changing a byte — the other outcome of a review, which used to be unrecordable. Saving an edit already counts. |
| `GET /v1/skills/{name}/history[/{stamp}]` · `GET /v1/skills/{name}/origin` | Every version a write displaced, and the full revision log. The skills page renders these as a diff + restore. `POST /v1/skills/{name}/history/{stamp}/restore` puts a version back in one call — history used to be readable and not restorable, which was tolerable only while every write to a manifest went through a person. The restore is itself a write, so putting back the wrong version is also reversible. |
| `GET /v1/runs/{key}/stream` | The open run as it happens — NDJSON, one object per line: an opening `snapshot`, the answer arriving as `delta` chunks (`kind: text` or `thinking`), each finished step, a `ping` every 15s of silence, and `done` when it settles, at which point it closes itself. Deltas are live-only and never recorded; the finished blocks are what the case file keeps. |
| `GET /ui` | The sessions page ([docs/operating.md](docs/operating.md)). Markup is served unauthenticated; the data calls it makes are not. |
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

Two documents, and the split is deliberate:

- **[docs/reference.md](docs/reference.md)** — every variable and its default,
  generated from `hookprobe/settings.py`. It cannot drift from the code.
- **[docs/configuration.md](docs/configuration.md)** — the same variables with
  the reasoning: why each one exists and what goes wrong without it. A generator
  has nowhere to put that.

The two you cannot skip: `HOOKPROBE_TOKEN` (empty means unauthenticated) and
`HOOKPROBE_EVENT_SECRET` (empty means the one door that starts paid runs is
open). Boot says so in the log when either is missing.

## Where the rest of it is

The front page answers *what is this, is it safe, and how do I call it*.
Everything below that has its own document, because a README that also holds the
reference is a README nobody finishes and a reference nobody trusts.

| | |
|---|---|
| [docs/operating.md](docs/operating.md) | deploying it, the sessions page, following up on an answer, development |
| [docs/configuration.md](docs/configuration.md) | every knob with the reason it exists; MCP servers by hand; browser evidence |
| [docs/cost.md](docs/cost.md) | what a run costs, why reuse is the lever, loop hygiene, model-call telemetry |
| [docs/learning.md](docs/learning.md) | case files, runbooks, skills, and the family loop that closes back onto the judge |
| [examples/patrols/README.md](examples/patrols/README.md) | patrol mode — scheduled investigations with no new code, and the two clocks that can drive them |
| [../docs/containment.md](../docs/containment.md) | every boundary in the family, each with a column for what it does **not** stop |

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
   namespace of its own either). The shipped compose files bound the blast
   radius: 2g memory, 512 pids, every capability dropped,
   `no-new-privileges` — a runaway analysis cannot take the host with it, and
   a compromised run finds no privilege ladder. (`ping` is the one casualty of
   `cap_drop: ALL`; curl and nc cover reachability.) The agent may write
   freely in `/data`, minus the carve-out below.
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

## Non-goals (v1)

- OpenClaw-dialect triggers poll; there is no `callbackUrl` on that contract
  yet. (Family event-door runs do report back — see The family loop.)
- In-flight runs do not survive a restart (finished results do). The caller's
  retry path covers this.
- No queue durability beyond the process: this runner is one container behind
  one orchestrator, not a job system.
