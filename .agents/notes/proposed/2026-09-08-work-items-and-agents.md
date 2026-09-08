---
title: WorkItem and Agent as product objects — stage one of the work-operations PRD
status: proposed
date: 2026-09-08
scope: hookprobe
---

## Decision

Built, in the narrow form only: a work item is **derived** from records that
already exist, an agent **describes** itself from settings it already resolved,
and neither gets storage of its own. A PRD arrived on 2026-09-08 proposing that
hookstack become an AI work-operations platform, with `Organization → Agent →
WorkItem → Session → Run → Artifact` as first-class objects, a nine-item
navigation, and a Runtime Contract behind which several agent runtimes could be
swapped. Its three headline decisions were: make Agent a first-class object,
promote the generic task to a formal WorkItem, and solve checkpoint/resume for
long sessions.

Stage one takes the first two and takes them *small*:

* `hookprobe/work.py` groups runs into work items and settles each one's state
  from the run records, the proposal files and the suggestion queue. Nothing is
  stored twice. `GET /v1/work` returns the board and its counts.
* `GET /v1/agent` answers what this node is: name, role, runtime, the policy it
  runs under, health. Two new knobs, `HOOKPROBE_AGENT_NAME` and
  `HOOKPROBE_AGENT_ROLE`.
* A `work_id` on every run, resolved by the event door from (1) what an upstream
  node stated, (2) the correlation the pipe already puts on every delivery,
  (3) the session key. The pipe learns one more field NAME and reads none of it.
* Two console tabs: `work` (the board) and `approvals` (everything waiting on a
  person). Both reuse the tab shell the three pages already share.

## Why

**WorkItem is derived, not stored.** Every fact a board needs is already written
down durably — the run record, the proposal file, the suggestion queue. A stored
work item would be a second copy of all of it, and the first time the two
disagreed nobody would see it. The cost is real and accepted: a work item cannot
carry a goal, an assignee or an SLA, because the loop does not know those. When
one of them earns its place it gets a file and `work.py` reads it too.

**WorkItem lives in the investigator, not the pipe.** The PRD puts the state
machine in the platform layer. In this stack that would be hookrelay, which is
content-blind by doctrine and capped at 5,500 source lines: deciding that an
alert is `waiting_approval` is a judgement about what the alert's investigation
proposed, which is exactly what the pipe refuses to know. The pipe gains one
field name it copies without reading, the same way it already carries `session`,
`thread_root` and `sender`.

**`work_id` is the pipe's correlation, not a new identifier.** The pipe already
stamps `X-Hook-Correlation-Id` on every delivery, and that is its handle for the
chain this hop belongs to. Adopting it means an alert, its verdict, its
investigation and its report share an id with no new wiring, and the console's
existing `#chain=` link keeps working. The one case it does not cover is a plan
handed to another node — a second chain — so the handoff payload carries the
work id explicitly and the receiving door prefers it. Two nodes agreeing on a
field name; no service learned about another.

**`received` and `triaged` are not states here.** The PRD lists both. A run in
this loop is executing the moment it exists — there is no queue to sit in — and
triage is the judge's verdict, which already has its own ledger and its own
page. A state nothing is ever observed in is a lie told once per reader.

**The north star is reported, not asserted.** `closed_unattended` counts work
that finished, was verified, and never had to stop and ask. Verification means a
ruling of `useful` or a procedure whose every step exited 0 — the only two
signals this node can honestly check. On an unattended deployment the number is
often zero, and that is the true reading: it says the verification loop is not
closed, which is a fact worth putting on a page rather than hiding behind a
softer definition.

**A memory suggestion does not block work.** It appears on the approvals page,
which answers "what waits on a person", and not in the blocked column, which
answers "what cannot proceed". Those are different questions and conflating them
would make the blocked count useless within a week.

## Consequences

**The pipe carries one more field name.** `work_id` joins `session`,
`thread_root` and `sender` on the return doors of both deployments and on the
handoff door — config, not code, and copied without being read.

**The board is computed on every request.** `GET /v1/work` re-derives from the
run list, the proposal directory and the suggestion queue each time. At the
scale these nodes run (a few hundred runs in the retention window) that is
cheap and the freshness is free. If it ever is not, the fix is a cache with an
invalidation on the same signal the live feed already publishes, not a table.

**Three numbers now have a place to be wrong in public.** `blocked`,
`verified` and `closed_unattended` appear on the console. If they read oddly the
derivation is the thing to argue with, and it is one function with the
precedence written out — which is the point of putting it there.

**Stage two is now the honest next step.** Everything above is a projection over
a loop that still loses a run's work when the process restarts. A board that
says `executing` and a process that cannot resume are a bad pair, and the board
makes that visible rather than fixing it.

## What this does NOT do

- **No Organization, no multi-tenancy, no Agent CRUD.** An agent is declared by
  its compose service and describes itself; creating one over an API is stage
  four's problem and would need a tenancy model that nothing here has.
- **No Runtime Contract.** `/v1/agent` names its adapter `claude-code` so the
  field exists before there is a second one, but the contract itself — with the
  two methods the PRD omits, a tool-gate hook and an audit hook, without which
  the containment claims do not survive a runtime swap — is stage two.
- **No checkpoint/resume.** The third headline decision is untouched. Today a
  restart sweeps a running run into `failed` and reports it (`sweep_orphans`),
  which completes the loop honestly but throws away work. That is the next
  stage's first task, and it is the one with real engineering risk in it.
- **No Artifacts tab.** Artifacts are listed on the work item that produced
  them. A separate page over three kinds of file, none of which anybody has
  asked to browse across work items, would be surface without a question behind
  it.
