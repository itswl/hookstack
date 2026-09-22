---
title: The work runner is told to do the work, not to plan it again — the handoff door speaks brief, and the runner gets a tree it may write
status: implemented
date: 2026-09-22
scope: stack
---

## Decision

Two changes to the work deployment's plan → work hop, both configuration:

1. **The `plan-approved` door speaks `kind: brief`**, and its `body` template is
   the work runner's instruction: carry the plan out, in order, verifying each
   step; read the planner's case file first; record changes as local git
   commits and never push; stop and name a gap instead of guessing. The plan
   itself follows the instruction under a rule, with the planner's session key.
2. **`probe-work` mounts `/data/code` writable** (`PROBE_WORK_CODE`, default
   `../work-data/probe-work-code`), the same path the planner reads read-only,
   so a plan's file paths resolve unchanged on the runner. What the variable
   points at is the blast radius for file changes; the first target is a scratch
   copy of one checkout, not the operator's tree.

Nothing else about the node moved: `danger-only` with its eight refusals, the
input guard, the egress proxy, the flight recorder, the $5/24h budget, the
signed doors, the read-only kube and cloud credentials.

## Why

**The hop existed and did the wrong thing.** Since the handoff shipped, a plan
a person handed off arrived at the runner as `kind: task`, and the task prompt
says, in its own words, *"Run one read-only investigation… how would this be
done?… Propose nothing for execution."* The runner answered with a second plan,
politely, at the runner's price. The comment on the door said the runner "is
being asked to DO the thing the plan describes" — the config's comment and the
node's prompt disagreed, and the prompt won. The operator's ask on 2026-09-22
was the sentence the comment already contained: *the planner's conclusion goes
to the work runner, and work does it, with its capability unrestricted and the
necessary interception and audit kept.*

**Why `brief` in the door rather than an `execute` kind in the node.** `brief`
already means *the body is the instruction* and already carries the 16 KB cap a
plan plus its instruction needs (a plan report runs 2–6 KB; the alert cap is
4 KB). Putting the instruction in the door's template keeps the pipe
content-blind — a template is not a judgement, the pipe reads none of it — and
makes "what the runner is told" a config reload rather than a service release,
which is the right speed for a sentence that will be rewritten several times
while the shape is being learned. When the wording stops changing, it can move
into `events.py` as a named kind with tests; not before.

**Why a writable mount, and why a scratch copy first.** The runner had nothing
to act on: its only writable path was its own workdir, and every mount was the
planner's read-only material. A runner with no tree is a planner with a longer
prompt. The mount is the operator's own line — "do not limit work's
capability" — and the tree it names is exactly the file-change blast radius,
which is the same shape `docs/deployments.md` gives the credentials: the
boundary is what you mount, not what the guard says. The first target is a
copy so the first run's commits can be read back before the real tree is
offered; switching is one `.env` line.

**What "necessary interception and audit" means here, measured against the
code rather than the wish.** Kept: the eight irreversible shapes `danger-only`
refuses (rm -rf, mkfs/dd, a fork bomb, container runtimes, terraform destroy,
namespace-wide deletes, host power), the always-on refusal of disabling the
egress record, the input guard on the files that steer the next run, the audit
line per tool call, the posture record, the budget breaker on this door.
Not kept, by design: nothing else. A `git push` is prevented by the absence of
a remote and a credential, and by the instruction — not by a guard, and the
note says so rather than implying one.

## Consequences

- **Rolled out to the local work deployment on 2026-09-22**: pull, recreate
  `probe-work` (new mount), `POST /config/reload` on the pipe. The alert
  deployment is untouched; the production host is untouched.
- **The first trial is scored by reading, not by the card.** The runner's
  report, its commits in the scratch copy, and `GET /v1/runs/{key}/audit` for
  what the guards refused. If the runner re-plans instead of acting, the
  instruction is the thing to edit, and it is a reload away.
- **`rm -rf` is refused even inside the scratch copy.** A plan that needs to
  clear a build directory will hit it; that is friction to watch, and the case
  for an `unguarded` posture if it recurs — a new note, not a flag flip.
- **The budget is $5 a day on this node.** A real execution run costs more than
  a plan; if the breaker trips mid-trial, `WORK_BUDGET_USD` is the knob and the
  refusal is on the board.
- **The real tree is one line away and should stay one line away** until a
  read-back of the scratch run says the runner keeps to the plan. When it does
  move, the tracked bot token in one project's git history
  (`hookstack-planner-code-mount` memory) is still the operator's to rotate.

## Rejected

- **Keep `kind: task` and ask the runner to act in the plan's text.** The
  prompt's own last paragraph forbids execution; a plan cannot out-argue the
  frame it is wrapped in.
- **Post the handoff straight to the runner's door.** One hop shorter and no
  ledger row, no dedup, no correlation — the reason `handoff.py` posts to the
  pipe.
- **Mount the operator's whole tree writable on day one.** Twenty-one repos,
  one of them carrying a live token in history, behind an instruction nobody
  has read a run against yet.
- **A new posture for this.** `danger-only` already permits every mutation a
  plan about a repository needs; what it refuses is not in any plan worth
  running unattended.
