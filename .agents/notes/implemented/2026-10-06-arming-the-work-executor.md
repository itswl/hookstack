---
title: Arming the work executor — gate files on the write node, and the press is the console
status: implemented
date: 2026-10-06
scope: stack
---

## Decision

The work deployment's write node (`probe-work`, `HOOKPROBE_BASH_GUARD:
danger-only`, the one service holding a credential that changes things) now
carries the remediation executor's two gate knobs —
`HOOKPROBE_REMEDIATION_ALLOWLIST` and
`HOOKPROBE_REMEDIATION_HIGH_RISK_ALLOWLIST`, both empty-defaulted — and a
read-only mount of the operator's gate files at `/etc/hookprobe/operator/`,
the same directory and mount path the production compose used. The files live
in `hookprobe/deploy/operator/` in the checkout, git-ignored beside
`decline-patterns.example`; a tracked `remediation-allowlist.example` now says
how to write one. Empty still means proposals collect and nothing executes:
wiring a knob arms nothing, it makes the operator's file nameable at all.

The approve press on this deployment is the **console** — `probe-work`'s,
published behind the doors ingress at `127.0.0.1:8090` — not a card button.
The pipe keeps minting only the planner's `handoff`: a card-action kind
forwards to exactly ONE channel, so one `approve` entry cannot serve both the
planner's and the runner's proposals, and pointing it at probe-plan would
answer a press for a runner-parked proposal with a 404 the pipe dead-letters
(proposals live in the workdir of the node that parked them). A card-borne
approve for the runner needs a kind split in hookrelay first; recorded as
open, not done.

`scripts/assert_knobs_are_reachable.py` gains `write_nodes_missing()`: every
service declaring `HOOKPROBE_BASH_GUARD: danger-only` must pass both knobs.
The check went red on the tree that motivated it (`deploy/docker-compose.work.yml:
probe-work lacks HOOKPROBE_REMEDIATION_ALLOWLIST`, and its high-risk sibling)
and green with the wiring, which is the whole of its test.

## Why

Step 3 of [[pilot-zero-read-back-and-the-order-of-the-next-ninety-days]] ends
"The allowlisted procedure and a credential scoped to one job are still open."
The knobs, the deny-by-default semantics, the two-file gate and the hot read
had all shipped and were tested; what was missing is that no service on the
only deployment that RUNS passed either knob. That is pilot zero's third lock
("no allowlist configured; proposals collect, nothing executes") re-created
one level down, with the same invisibility: "reachable from a compose" was
satisfied by the demo stack, and every existing check asked the repository
question, never the deployment one. Found by a human reading a plan; the new
check is so the next absence is found by the gate.

The console is a real press path and was already there: `POST
/v1/remediations/{proposal_id}/approve` takes the identical service method a
card press takes, the ingress publishes all three consoles, and lock 2 ("the
console was not reachable from where the cards were read") has been lifted
since the boards and consoles install on a phone. The card half is the part
genuinely absent on this deployment, and it is absent for a structural reason
worth recording rather than working around in config.

Where proposals come from on this deployment: the parking lift is
node-agnostic and kind-blind — every completed engine run's report may carry a
fenced `remediation` block into its own node's workdir. The invitation is
carried by the alert/default prompt and, for every door, by the operator
methodology file the repo ships as an example
(`hookprobe/examples/system-prompt.md`), installed untracked at
`{workdir}/system-prompt.md`; the task door deliberately forbids proposing,
and the brief door is neutral — so a runner report proposes when the brief or
the installed methodology asks it to, and the operator's first procedure
should be driven that way, on purpose.

## Consequences

- Arming is operator-side and additive: one line into
  `hookprobe/deploy/operator/remediation-allowlist`, the two paths into the
  deployment's `.env` by their in-container names, a recreate — and the first
  write credential is minted scoped to its job rather than the administrator
  profile (the node runs on the planner's read-only fallback until then). The
  compose comments beside those lines carry the ordered how, including the two
  permissions a scoped policy must not omit: `eks:GetToken` (the shared
  kubeconfig authenticates through it) and `iam:SimulatePrincipalPolicy`
  (without it the posture check records `unverifiable` instead of a measured
  radius).
- The danger-only half of `HOOKPROBE_POSTURE_CHECK` had no test reaching its
  refusal — the one test ran `warn` with a fixture the parser discards as the
  table header — and "a writing posture is only ever recorded" in `settings.py`
  and `configuration.md` was true only of an UNDECLARED node. Both fixed in
  this change: the refusal has a test that fails without it, and the docs say
  what the code does (a declared writing node is judged exactly as a readonly
  node is).
- `deploy/work.yaml` carried a stale orphan comment ("No card_actions") above
  the actual `card_actions` block; replaced with the truth and the console
  pointer.
- Still open, each recorded rather than silently dropped: every step
  listed on a proposal card (the button stays one line by design); the
  per-plan ephemeral worker ("after step 3"). And the first press itself —
  until it happens the gate is armed and unexercised, which is the state this
  note exists to make visible rather than let pass for done.

Related: [[execution-success-is-not-recovery]],
[[three-gaps-before-the-execution-door-opens]],
[[one-pending-proposal-per-procedure]].

## The card's approve, 2026-10-08

The kind split this note waited for landed: a card action may be routed by the
door its card came in through (`forward_by_source`), and the work deployment
routes `approve` from `work-notify` to a new `to-work-action` channel, the
runner's action door, signed with the runner's own secret. A planner card gets
no approve, because nothing executes on the planner. And the investigator
declares `approve` only on a node whose allowlist has a rule — a press anywhere
else is always refused — so the runner's cards will carry the button from the
first allowlisted line on, and not before. The console stays the other press.
