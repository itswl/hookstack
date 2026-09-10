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

## A restart continues an investigation instead of losing it

The engine's transcript lives on the data volume, not in this process. A run in
flight when the service restarts — a crash, an OOM kill, a redeploy — used to
become a failed run that reported itself, and an operator re-asked the question
by hand, paying for the whole investigation twice. At startup the service now
**continues** such a run in its own engine session, so everything the
interrupted attempt gathered comes with it.

Four bounds, because this is the one path that spends money with nobody asking:

| bound | what it stops |
|---|---|
| a session id must exist | nothing to continue; the run fails and reports, as before |
| one resume per run, counted on the run | a crash loop becoming a spend loop |
| the budget breaker | a restart spending past the window's ceiling |
| `HOOKPROBE_RESUME_INTERRUPTED=off` | any automatic spending after a restart, for a deployment that wants none |

The id is what makes it possible, and it is recorded **mid-turn**: the runtime
publishes it as an event the moment it first says it, and the service
checkpoints it to disk immediately. Recorded from the engine's result instead —
which is where it used to come from — a first turn cut off before it finished
had no handle to continue, which is exactly the case worth recovering.

The lost attempt is kept as a turn of its own, priced `null`: the provider
billed whatever it billed and no result ever came back to say. It counts in the
unpriced-turn figure, where it belongs, rather than being quietly dropped or
recorded as free.

**A provider blip is retried once, at the moment it happens.** Two real alert
investigations on this deployment died on `API Error: 524` — a gateway timeout
— and sat in the board's "needs a human" column for four days; by the time
anybody read it, re-investigating meant paying for a question whose answer had
stopped mattering. A failure the engine reports is now classified, and a
transient one buys one more attempt after five seconds, continuing the session
if the first attempt left one.

What counts as transient is a short list (`engine.transient`) and the default is
**permanent**: a status code that means "later" — 408, 429, 5xx, Cloudflare's
52x — plus overload, rate-limit and connection-reset wording. Not on it: a
context-window limit, an insufficient balance, an auth failure, and the words
"timeout"/"timed out" on their own, because a wall-clock timeout is this
service's own limit rather than a provider blip and the second attempt would
want the same extra time the first one did. Both attempts are turns in the
record with what each cost, so a failure that burned tokens before the gateway
gave up is in the ledger and the budget breaker sees it.

For the failures nothing automatic picks up — a timeout, a second blip, a
restart with no session to continue — `POST /v1/runs/{key}/retry` is the human
takeover, and the work board's **needs a human** column has the button. It
continues the engine session when there is one, re-asks the opening question
when there is not, and is not budget-gated: a person's explicit request should
not bounce off a meter.

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
| **needs a human** | the last run failed within the last two days — including a refusal for budget |
| **in flight** | a turn is running |
| **verifying** | a procedure ran and every step exited 0, and nobody has said the condition cleared |
| **done** | finished with nothing blocked |
| **abandoned** | it failed and nobody came back to it inside two days |

`blocked` is the first two columns only. **Abandoned work is a count, not a
queue**: this board opened on production with twelve items in *needs a human*,
of which two were worth acting on and the oldest was three weeks old. A number
that mixes "somebody should look at this today" with "nobody ever did" is
useless as the thing an operator reads in the morning. Two days is the window,
from the evidence that set it — an investigation that died on the 4th was
already answering a question nobody was asking by the 8th. The retry button is
on both columns; choosing to reopen an old failure is a person's call, and the
column name is what makes it an informed one.

A line above it says what this node IS — name, role, guard, model, what it is
running right now, and in red the count of reports that never reached the pipe,
which is the one health signal that means an agent is failing silently. Nothing
read `/v1/agent` before; a deployment runs several probes and the only way to
tell which one a board belonged to was the port in the address bar.

Then the numbers. `blocked` is the first two columns.
`closed without anyone stepping in` is the strict one: done, verified, and it
never had to stop and ask. `% ended without an answer` is the failure rate,
counted over **work** rather than runs: a run that failed and was retried into
an answer is not a piece of work that failed, and abandoned work is, because
nobody came. A filter sits above the columns, because a board with 184 items in
one of them needs a way to reach one of them.

Those six figures are the whole of what the PRD asks a product overview to
answer. There is deliberately no separate Overview page: five of the six were
already here, and a ninth navigation item repeating them would be a second
place for the same numbers to be wrong in. Aggregating several nodes onto one
page is a different thing and is not built — see the note in
`.agents/notes/proposed/`.

**Verified** means one of three things, and the board says which:

| `verified_by` | what happened | how strong |
|---|---|---|
| `ruling` | a person pressed *found the cause* | a human read the report and agreed |
| `remediation` | the procedure the report proposed was approved, ran, and every step exited 0 | the work's own actions succeeded |
| `recovery` | the condition the alert was about has since ended | the episode is over — **not** proof the investigation was right, or that the agent caused it |

The third is the only one that needs nobody, which is what makes the number mean
anything on an unattended deployment. It arrives on its own: the judge sends a
recovery verdict for the ended condition, the event door recognises the stated
`is_recovery` flag and records it on the investigation of the same condition
instead of starting one. That costs nothing and replaces what used to happen —
the recovery was read as a RE-FIRE and bought a turn telling the model the alert
had fired again when it had in fact cleared. A recovery is matched by condition
name within a day, which works because the judge strips the "it ended"
decoration before sending, so a firing and its recovery arrive under one name.
A recovery for a condition this node never investigated is a named skip.

`/ui#approvals` is the other half: every procedure waiting for approval, every
memory line an investigation proposed, and every report nobody has ruled on, with
the buttons. It answers "what do I owe", which is deliberately wider than "what is
blocked" — a memory line blocks nothing and still waits for a person.

**An approved command sees only what it needs.** `execution_env()` passes
`PATH`, `HOME`, locale, the cloud credentials an operator mounted for the
purpose (`AWS_*`, `KUBE*`, `GOOGLE_*`, `AZURE_*`) and the egress proxy
variables — and nothing else. Until 2026-09-10 it passed no environment at all,
which meant an approved procedure ran with the family's HMAC signing keys, the
Lark app secret and the provider credential in scope. Three things stood in
front of that — a deny-by-default allowlist, a human click, and no shell — and
none of them is a reason to hand a procedure keys it does not need. The agent's
own shell had been scrubbed on exactly this argument; the one path that
actually executes had never been.

An allowlist here rather than the agent's denylist, and the difference is the
point: a denylist names the secrets this repository knows it holds, which is
right for a process that must keep working with everything else. A procedure's
needs are known and short, and what a denylist cannot cover is the secret a
future deployment adds under a name nobody wrote down. If the list is wrong the
command fails with its own error in the results an operator reads — where one
quietly carrying a signing key leaves no trace at all.

**A procedure expires after a day.** Approving one runs commands chosen from
evidence gathered at one moment — *suppress this address*, *restart that unit* —
and approving it a week later runs a decision about a system that has since
moved, which the person pressing cannot see from the card. The card's action
token has always expired after 24 hours; the console had no equivalent, so a
three-week-old proposal was one click from a shell. `remediation.approve` now
refuses past the same 24 hours and says how old it was, and the board stops
offering it: a stale proposal leaves `blocked`, because `blocked` has to mean
work somebody can clear now. It stays visible on the work item, marked
*expired unapproved*. Getting the procedure run means asking for a fresh look,
which is the honest answer — the investigation is what has gone stale, not the
button.

**And a procedure the condition outran is retired, whatever the clock says.**
The window above is a proxy: it assumes the world moves at a rate. The cursor is
the world itself, as far as this node can honestly see it. Every proposal records
two things about its condition at the moment the steps were chosen — whether it
had ENDED, and which turn of the investigation wrote them — and both are read
again when somebody approves. A recovery arriving in between is the case this
exists for: the alert cleared, nobody watching the chat can tell, and the button
still says *approve & run*. Then the row goes to `superseded` and nothing
executes.

It is checked in two places because they catch different things. The button is
not drawn at all when the cursor has already moved — a follow-up report is
delivered under a new turn, so every proposal from the turn before it is
automatically past — and `remediation.approve` refuses the race the card cannot
see, between the card being sent and the press arriving.

A refusal at that point has nowhere obvious to go, which is worth knowing if you
are reading a chat and not a board: the bridge repaints a pressed card
"accepted and passed on" the moment the PIPE takes the press, and strips the
buttons on the way out, because their token is single-use. So the refusal comes
back the way the budget breaker's does — as a report through the family loop,
into the same conversation, saying what moved and that nothing ran. The way to
get the work done from there is the follow-up button beside it: it
re-investigates and proposes against the world as it is now.

What it does not see: anything that did not come through this pipe. Somebody
fixing the target by hand and saying nothing in chat is invisible to it, and
deliberately so — this node holds no credentials for the systems it writes
procedures about, and a freshness check that opened one would be a second,
unaudited way of touching them.

**And a target is left alone for a quarter of an hour after something acted on
it.** The clock and the cursor both answer *has the world moved since these
steps were written*. Neither answers *did we already do this to this box twenty
minutes ago*, which is what a flapping condition asks — and a fix and the
rollback of that fix could both be approved inside a minute, the second acting
on a machine the first had just changed and nobody had looked at since.

So an approval is refused while another procedure's target is cooling
(`HOOKPROBE_REMEDIATION_COOLDOWN_SECONDS`, 15 minutes, `0` disables), and a
procedure still executing holds its target with no window at all. This refusal
is the one that is **held, not retired**: nothing about the proposal is wrong,
so the row stays `proposed`, the console lists the reason beside it with reject
still enabled, and the same approval works once the window passes. Its report
back into the chat says *check what the earlier procedure changed, then approve
this one again if still needed* — which is the actual work the cooldown is
buying time for.

A step is held by its declared `target` or by its literal command, either one
matching. Both, because production showed the label alone is not enough: five
proposals there name one thing three ways — `AWS SES 账户状态`, `AWS SES account
status`, `AWS SES 账户状态（只读）` — while running character-identical commands.
The command is the half that holds: a procedure runs verbatim in this
container, so two identical strings are the same action whatever they were
called.

**What happens if somebody just keeps replying.** Not what most people expect:
the conversation hits the **follow-up cap** long before the context window. Each
reply continues the same engine session — which is the cheap direction, measured
at $0.0156 against $0.1490 for a fresh investigation on the same context — and
after `_MAX_FOLLOW_UPS_PER_RUN` (20) answers, the next one is declined.

Every refusal in that door used to be silent. It returned a 200 with a reason,
the pipe recorded the reason in its ledger, and the chat learned nothing, so the
twenty-first question looked exactly like a broken bot. Three of them now answer
in the thread — the cap, a spent budget, and a session that can no longer be
resumed — because those are the three a person can act on. An unknown thread, a
redelivery and an unlisted sender stay quiet on purpose.

**And a report can outlive the condition it is about.** An investigation takes
a minute; a recovery arriving during that minute is recorded on the run —
`record_recovery` annotates and spends nothing, so it never becomes a new turn —
and then the report is delivered on schedule, recommending work for something
that is over. The card used to be identical either way. It now carries a line
saying the condition ended, and whether that was before or after the report was
finished, because only the first makes the findings stale.

It is an admission, not a suppression: a flapping alert clears on its own and
will be back, the findings may still be worth reading, and a procedure proposed
there may still be the right thing to run. What the reader gets is the one fact
they could not see — that the answer was written about a moment that has passed.
Note the boundary: the freshness cursor guards the APPROVAL of a procedure, and
a proposal born after the recovery is stamped as already-recovered, so its
button is still drawn. This line is what tells the person pressing it.

Before the cap, the runtime folds its own context away when it fills. That has
always been recorded and shown on the console's turn line (`context folded 2×`);
the card now says it too, in one line under the summary. It is not a claim that
the answer is wrong — a folded conversation is usually fine — it removes the
assumption that the answer saw everything. Note that context *fullness* is
unavailable on this deployment: the CLI does not answer the usage request, so
`run.context` is null and the fold count is the only signal.

**`GET /v1/selftest` — the node demonstrates its claims instead of describing
them.** `/healthz` says the process is up. `/v1/posture` says what the
credentials allowed at STARTUP. `/v1/agent` says what the settings asked for.
All three are descriptions, and the failure this service keeps meeting is not a
boundary breaking — it is a boundary being **absent while every surface still
reads fine**: a spawned gate that could not import its own package, so a
`kubectl delete` ran on a node reporting `bash_guard: readonly`; an egress
allowlist whose bypass was one shell prefix for the first hours of its life; a
price knob no compose could pass.

So this one does the things, now, and reports what happened: it hands the gate
a tool no posture permits (in process, and again through the subprocess path);
it hands the shell guard a mutation and an egress bypass; it asks this node's
own proxy for a name nobody listed; it presents the AGENT's bearer to a write
route; and it re-measures the credentials rather than reading the boot record,
because one widened after startup moves nothing that anybody reads.

It spends nothing and runs no model. Two properties are the point:

* **A check that cannot run reports `held: null` and is listed under
  `unproven` — never a pass.** A green board assembled out of checks that
  quietly skipped is the same failure it exists to catch.
* **Every check names what it does not cover**, the way each row of
  [containment](../../docs/containment.md) does. A check that only reports a
  pass teaches its reader the boundary is total.

**The audit trail is chained** (`hookprobe/audit.py` — the record; `gate.py` is the decision). Each line carries the hash of the one before it,
so an edit, a deletion or a reordering afterwards stops the chain and can be
pointed at — `verify_chain` names the first line that stops adding up, and the
selftest walks it. That row reported `null` — *not built* — from the day the
endpoint shipped, which is what made it worth building: the gap sat on the same
page as the boundaries that held.

Three outcomes, told apart on purpose. **Intact**: every linked line's digest
recomputes and names its predecessor. **Broken**: the first offending line is
named, and everything after it is unverifiable rather than wrong. **Unchained**:
lines written before this existed, counted and never treated as a break — every
deployment has history from before, and an alarm that fired on all of it on day
one would be ignored by day two.

The digest is over canonical JSON, not the bytes on disk, so a reader that
re-serialises differently still verifies: the record is the facts, not the
formatting. Writing is serialised with a lock on a chain file rather than the
day file, because the gate is spawned per tool call and two writers racing on
the same predecessor is the ordinary case. **And if the chain cannot be kept —
a locked file, a read-only mount — the line is still written, unchained.** A
missing audit line is worse than an unverifiable one; verification reports the
gap, where a writer that dropped the record leaves nothing to report.

**And the chain head goes off-box with every report.** Chaining alone is evident
only on this disk: whoever can rewrite the audit can rewrite `.chain` beside it
and rebuild something perfectly self-consistent, which `verify_chain` accepts —
that is asserted as a test, because it is the limit somebody would otherwise
have to discover.

So each report that goes home carries `meta.audit_head`: the chain head as it
stood when the report was written. The pipe keeps that payload in its own
ledger, on its own disk. A record rewritten here later fails twice — the local
chain stops adding up, and a head the pipe wrote down hours ago no longer names
any line this node has. `gate.chain_anchored(audit_dir, head)` asks exactly that
question, and it is the half an editor cannot forge, because rebuilding a chain
is easy and rebuilding one that still contains somebody else's recorded hash is
not.

A hash and nothing else leaves: no content, and the field is empty on a node
that has chained nothing yet.

What it still does not stop: somebody who can rewrite the audit here **and**
reach the pipe's ledger. Two disks and two services is the bound this buys, not
proof.

`GET /v1/agent` says what this node is — name, role, runtime, policy, health — so
a deployment running an investigator, a planner and a work runner can tell them
apart by something other than a port. Set `HOOKPROBE_AGENT_NAME` and
`HOOKPROBE_AGENT_ROLE` per service.

`GET /v1/agent/description` answers a different reader. Same node, in ANP's
Agent Description dialect: what a caller can ASK for, and — in that schema's own
`humanAuthorization` field — which doors will not move without a person. It
says strictly less than `/v1/agent`, on purpose: no model, no gateway endpoint,
no workspace, no budget, because a document meant to be crawled is the wrong
place to widen what a caller learns, and one of those is an estate identifier.
`HOOKPROBE_PUBLIC_URL` is where this node can be reached from outside; unset —
the default, and true of every deployment here — the interfaces carry paths and
the document says `reachable: false` rather than publish a loopback address that
would resolve to the caller's own machine. The route is token-guarded like
every other, which for a node with no reachable address costs nothing. Why the
rest of ANP was not adopted is in
[`.agents/notes/proposed/2026-09-09-anp-evaluated-the-identity-layer-needs-a-public-origin.md`](../../.agents/notes/proposed/2026-09-09-anp-evaluated-the-identity-layer-needs-a-public-origin.md).

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
