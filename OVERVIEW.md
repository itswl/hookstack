# hookstack — an overview

hookstack puts agents on a team's production signals without handing them the
keys. A signal arrives; a pipe signs, routes and prices it; an agent
investigates it read-only, measured read-only at startup; anything that writes
waits for a person; and the whole path leaves a priced, audited record. The
investigator is the product. The pipe and the judge are the smallest alerting
front end for a team that has none.

This page follows an alert, but the alert is where the design started, not its
limit: a chat message, a ticket or a timer take the same path on the same code
([two deployment shapes](docs/deployments.md)). The alert deployment ran in
production until 2026-09-23.

Every screenshot is from a local run started from nothing on 2026-09-30, not a
mockup. The investigator's pictures come from five real investigations on
`gpt-5.6-luna` ($1.10 together, through a gateway speaking the Anthropic
dialect). The rest come from the no-key demo and the stack smoke.

```
upstream alert sources (Grafana / Alertmanager / cloud monitoring …)
      │
      ▼
  hookrelay :8100 ──► hookjudge :8200 ──► hookrelay ──► chat bridge ──► Feishu (DingTalk / WeCom via plugin)
  (pipe: adapt+route+ledger) │ (judge: verdict+cost)   (formats and delivers)
                             │
                             └──► hookprobe :8088 ──► hookrelay ──► the same channels
                                  (investigator: read-only by default)  /hook/probe-notify
```

## Who does what

| Component | Does | Deliberately does NOT |
| --- | --- | --- |
| [`hookrelay/`](hookrelay) | The pipe. Adapts every upstream dialect into one event, routes it, turns verdicts and reports into a neutral card model, and accounts for every hop | Understand content, or judge |
| [`hookjudge/`](hookjudge) | The judge. One event in, one verdict out, by five routes in cost order: recovery, reuse, rule-reuse, ai, rule | Render cards, or know channels |
| [`hookprobe/`](hookprobe) | The investigator. One read-only agent run per important alert, a root-cause report back, follow-ups in the same session | Receive alerts, or send notifications |

A per-platform bridge renders the card model ([docs/bridge-protocol.md](docs/bridge-protocol.md)),
so nothing in the pipe or the brains names a chat platform. The judge answers
"is this worth interrupting a person for" in seconds; the investigator answers
"what happened" minutes later, into the same channels.

## hookrelay: the pipe

Upstream dialects are adapted in config: placeholders lift title, body and
level out of the payload, and `level_map` puts every vendor's words on one
scale. Every event goes to the judge; important ones are copied to the
investigator; their returns take routes straight to the channels, so a result
is never processed twice. The ledger keeps the payload as received and the
exact body of each delivery (never the headers), so `/trace/{id}` settles a
receiver's dispute from the record. When the pipe itself breaks, a dead-letter
self-alarm goes straight to an operator bot.

![hookrelay ledger: every event with its decision chain, its deliveries, and what came back](docs/img/hookrelay-ledger.png)

The ledger is the forensic view. The open alert shows its decision gate by
gate, both deliveries, and what came back: the verdict a second later, the
report three seconds after. The red squares above it are deliveries that
failed while the chat was down; the silenced Kafka alert below was recorded
and ignored.

![hookrelay's board: one sentence on whether anything waits on you, four numbers, and one row per alert — in flight, a delivery that failed, cards waiting on you, a recovery — each with its seven stages](docs/img/hookrelay-timeline.png)

The board reads the same ledger like an inbox. One sentence says what waits on
you, four numbers open the list pre-filtered, and every row is one alert with
seven stages: received, judged, notified, investigated, a person, condition,
fix. In this run one alert is in flight, one failed to deliver, seven wait on
you, one was ruled useful, one recovered and one fix held; the picture is the
top of that list. A row opens the alert's whole story in a drawer, down to the
bytes of both directions and the audit record.

## hookjudge: the judge

One event in; one verdict (importance, type, one-sentence summary) back to the
pipe; the cost recorded. The routes are tried in cost order: **recovery**
inherits its firing's verdict, **reuse** re-serves the last verdict for a
restated condition, **rule-reuse** re-serves an alert rule's last AI verdict,
**ai** is the one paid call, and **rule** is the keyword floor when the model
is unusable. The saving comes from most events never reaching `ai`.

![hookjudge's board: nine verdicts, every free route exercised, four of them paid, four disagreements waiting for review](docs/img/hookjudge-status.png)

After the demo plus four more alerts, judged by the stub model: 9 verdicts, 4
paid, $0.001 (the stub prices tokens like a real model), zero failed returns.
Every gateway alert arrived `high` from its platform and left `critical`, so
the board opens on four disagreements waiting for review, one click each.

## hookprobe: the investigator

It takes a task, runs one tool-using agent, and returns the text, behind the
OpenClaw-compatible trigger/poll contract (`POST /hooks/agent`,
`GET /sessions/{key}/final`), so a caller already speaking it switches by
changing a URL. The engine is the Claude Agent SDK; hookprobe owns no agent
framework code. A crash, a timeout or a Stop still settles as a well-formed
report the caller sees on its next poll.

Read-only is built in layers, strongest first:

1. **Credentials.** Only read-only principals are mounted.
2. **The bash guard** refuses mutating verbs (kubectl, helm, systemctl, terraform, ssh) before they run, subagents included.
3. **The container** is non-root and disposable.
4. **The input guard** stops a run editing what steers the next one.

The posture is declared per node and measured against the credentials at
startup; a node wider than it declared refuses to start. The one node meant to
change things runs `danger-only`, where the credential is the blast radius and
nothing arrives without a person's signed click.

![hookprobe's sessions tab: five real investigations on the left, the payment-gateway report on the right, conclusion first, root cause unknown and said so](docs/img/hookprobe-sessions.png)

The console lists sessions on the left and the conversation on the right, with
each turn's bill (gpt-5.6-luna · in 1.5k · out 1.4k · cache 55.6kr/21kw ·
$0.2137 · 25.1s). The report opens on its conclusion and says what it could
not establish: the host is fictional and unreachable, so the root cause is
**Unknown**, and the possible causes are named as inferences, not findings. It
searched the case files first, ranked the remediation, and refused to propose
a command it could not justify. Below it: the two rulings a person can press,
and the button that distills the run into a runbook draft. A follow-up in the
box at the bottom resumes the same session with everything it gathered.

![audit view: every tool call in every run, subagents included, newest first](docs/img/hookprobe-audit.png)

The audit view is a flight recorder the agent cannot edit: one line per tool
call across all runs. Here, 23 calls: the five runs reading each other's case
files and grepping for their alerts' terms.

![the waterfall: model and tool calls of one investigation on one time axis](docs/img/hookprobe-waterfall.png)

The CLI reports every model call and tool result to the service itself, and
the run's page draws them on one axis. This run took 25 seconds, all of it
waiting on the model across five rounds; its two costliest calls were 72% of
its $0.21.

![skills browser: the diagnostic runbook distilled from the disk investigation, its six lookups and what they found](docs/img/hookprobe-skills.png)

One press of *Distill into a runbook draft* had the service, never the agent,
write down the six lookups the rehearsal's disk investigation made; a person's
Save put it on the volume with provenance, and the next disk alert starts from
it. Runbooks are the SKILL.md format, so a downloaded skill installs by
unzipping it into `.claude/skills/`.

![environment memory editor: CLAUDE.md reaches every investigation](docs/img/hookprobe-memory.png)

The environment memory (CLAUDE.md) reaches every investigation. The demo's says
the alerts' hosts are fictional and unreachable and that a reading nobody took
must never be reported, and the report above is that memory arriving at the
model: "the host is fictional/inaccessible", "Root cause: Unknown". The prompt
view beside it holds the methodology; both are read fresh at every run.

Four more things fill it out:

- **Case files as episodic memory.** Every investigation's record stays on the volume, and the brief tells the agent to open earlier ones of the same alert first.
- **Subagent roles.** One `.claude/agents/*.md` file per role, delegated to through the Task tool.
- **MCP servers and host skills.** `mcp.json` is read fresh at every run; a host skills library mounts read-only.
- **Accounting.** A budget breaker on the door that spends without a person asking (a refusal still sends a card), and the system view below with secrets shown as set or unset, never their values.

![system view: the whole runtime, from model and budget to MCP servers and health](docs/img/hookprobe-system.png)

## The loop: escalation in, report out

The pipe copies every front-door event to the investigator's `/hooks/event`.
The investigator decides by level what is worth paying for (critical and high
by default), funds one investigation per event however often it is
redelivered, and returns the report to the pipe's `probe-notify` door, signed.
The pipe dresses it as a card for the same channels as the verdict. A failed
investigation completes the loop the same way: a timeout, a crash, even a
SIGKILL settles into a report, and a spent budget refuses out loud with a card
saying why.

A crontab that posts "patrol due" to the same door turns it into a scheduled
investigation with no new code. Two such patrols ship as briefs in
[`hookprobe/examples/patrols/`](hookprobe/examples/patrols/README.md): whether
the noise is going up or down week on week, and which nightly condition to
propose silencing. Both propose; neither acts.

## The loop, rehearsed: from report to audit record

Four pictures of one loop, from the no-key demo: a recorded investigation
replayed through the real read-only gate. Every number in its report was
written in advance, and the report says so; everything else happened.

![the investigator's session page: a rehearsal run, one call refused by the read-only gate, the report ending in a two-step procedure](docs/img/hookprobe-rehearsal-run.png)

The report the card was written from. `refused 1` is the recorded
`kubectl exec` meeting the real guard. The run cost `$0.0000`, and its
procedure proposes two observations rather than the fix, so approving it can
damage nothing.

![the actions page: the procedure executed, both steps exit 0 with their output, held because the condition ended, approved by a named actor](docs/img/hookprobe-actions-held.png)

What the approve press did: both steps ran as argv with their exit codes and
output, and the procedure **held**, because the condition ended afterwards.
Exit 0 alone verifies nothing; the recovery is the witness.

![the work board: two pieces of work done, one verified by its procedure, none closed without a person](docs/img/hookprobe-work-board.png)

One card per piece of work. The disk investigation is `verified ·
remediation`, and the header reads `0 closed without anyone stepping in`: an
approval is a person stepping in, and the board says so.

![the disk alert's story on the pipe's board: seven stages top to bottom — the verdict, six cards and what each carried, the report, the approve press named with its actor, the condition ending, the fix held](docs/img/hookrelay-audit-press.png)

The same alert from the pipe's side: received, judged, six cards notified,
investigated, approved by a named person, the condition ending, the fix held.

## Running it locally

One command starts the whole stack, the investigator included, on the
rehearsal, for $0; a model key makes it real. Each service's gate is a local
replica of its CI job.

```bash
# the whole stack: pipe, judge on the stub model, investigator on the rehearsal, sink
git clone https://github.com/itswl/hookstack && cd hookstack
docker compose up -d --build      # relay :8100 · judge :8200 · probe :8088
bash scripts/demo.sh              # the loop end to end, for $0
bash scripts/stack-smoke.sh       # or the whole smoke check
```

```bash
# make the investigator real (any Anthropic-dialect endpoint works:
# ANTHROPIC_BASE_URL + ANTHROPIC_AUTH_TOKEN instead of the key)
printf 'HOOKPROBE_RUNTIME=claude\nHOOKPROBE_MODEL=claude-opus-5\nANTHROPIC_API_KEY=sk-ant-...\n' >> .env
docker compose up -d --build
open http://127.0.0.1:8088/ui
```

`<service>/deploy/docker-compose.yml` runs one service on its own, and
`deploy/docker-compose.yml` runs all three with real credentials. Both are run
from the repository root with `docker compose --env-file .env -f <file> up -d --build`.

## The loops that tighten

Four feedback loops are what the stack is for:

- **Attention.** The judge also answers whether a person must act *now*. An explicit "no" drops the card but keeps it on the boards, an unanswered verdict fails open into a card, and a regret counter tracks the one failure that matters: a quieted alert a person later ruled worth having.
- **Money per verdict.** One paid verdict answers a storm of restatements, and a recovery inherits its firing's verdict.
- **Money per investigation.** Runbooks answer the re-fires of a condition ruled not worth it, at no cost, and it still gets a real investigation on a schedule.
- **Judgement quality.** A golden set of labelled production incidents replays through the judge's prompt on every deploy; a prompt that under-calls one does not ship.

Where a loop needs a person who never comes, patrols infer the answer and file
it marked as inferred.

## Where this stands

The alert shape ran unattended on a production stream from August until
2026-09-23, when the operator retired it; `deploy/shadow.yaml` stays here as
its worked example. What runs today is the work deployment, on the operator's
own machine. The approve-and-run path for a proposed procedure is rehearsed end
to end and has not yet run against a write credential. The handoff path has: on
2026-09-30 a plan a person handed off installed a scheduled log cleanup on three
nodes of a test cluster, with a write credential mounted on the work node for
that run and taken off afterwards
([how it went](docs/deployments.md#the-first-real-write)).

## Where this sits in an AI-native SDLC

Anthropic's [AI-native SDLC playbook](https://claude.com/blog/the-ai-native-sdlc-playbook)
calls this stage **Maintain**: cheap deterministic answers before a paid
model, an investigator only where one is earned, remediation proposed rather
than applied, every decision in a ledger. [How Anthropic secures its own
AI-native SDLC](https://claude.com/blog/how-anthropic-secures-its-ai-native-software-development-lifecycle)
treats agents as monitored actors rather than trusted authors, and so does
this: the investigator proves it is read-only at startup, cannot edit what
steers its next run, and a red-team run drives injections at its memory path
against a live model.
