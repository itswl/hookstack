---
title: The approval is on the card the plan was read on
status: implemented
date: 2026-09-28
scope: stack
---

## Decision

A seventh card action, `handoff`. A planner wired to a handoff door
(`HOOKPROBE_HANDOFF_URL`) declares it on every finished plan — first on the
card, labelled *Act on this plan* — and the press does exactly what the
console's *Hand off* button does: posts the plan, signed, to the pipe door the
runner listens on. Both paths run one method, `service.hand_off`, so they post
the same bytes and leave the same record: `handed_off_at` and `handed_off_by`
on the plan's run, one audit line, and no second offer of the button once the
plan is handed off. The press's answers follow the action door's rule — a
plan that is gone or empty is a 202 with a reason, a runner with no door named
is a 501 (the operator's to fix, so the pipe alarms), a pipe that would not
take it is a 502 (worth the retry).

The work deployment gets the wiring: a `to-plan-action` channel to the
planner's action door, `card_actions: {handoff: {forward_to: to-plan-action}}`,
and `HOOKRELAY_ACTION_SECRET` on the pipe from `WORK_ACTION_SECRET` in `.env` —
empty keeps the cards buttonless, which is the state the deployment was in.

## Why

The operator reads plans on a phone. Until now the one human step in the work
chain — "I have read this plan and I want it carried out" — could only be
taken on the planner's sessions page, on the Mac. The plan arrived where the
person was and the answer had to be given somewhere else; on a day with three
plans that is three trips to a laptop for three clicks. Asked what mattered
most, the operator said their own use. This is the step that cost them most.

Everything needed already existed: the bridge sends presses back, the pipe
mints and verifies buttons and forwards presses through the outbox, the
planner has a handoff path. Nothing had connected them, and the work pipe's
config had no `card_actions` at all — which is also why the board's "waiting
on you" read zero on that deployment: no card had ever asked anything.

## Consequences

- A work chain can now close on the phone: signal → plan card → *Act on this
  plan* → the runner's report in the same thread → (when a procedure ran) the
  verdict. The pipe's journey shows the press as **a person · handoff** with
  the presser, and the runner's answer after it.
- The presser is recorded as the platform's opaque id, the same way every
  other press is. The board reads it as *somebody*; the run and the audit line
  keep the id.
- Dedup is still the pipe's: a second press after the claim window arrives
  as the same `plan-approved` event and is recorded as a duplicate, exactly as
  a second console click always was.
- `WORK_ACTION_SECRET` is one more value in `.env` that must be set for the
  buttons to exist and must never be committed. It is the same class of
  secret as the demo compose's `HOOKRELAY_ACTION_SECRET`.
- `followup` is deliberately not on the work cards yet: the pipe forwards a
  kind to ONE channel, and a follow-up on the runner's card would land on the
  planner. Thread replies already reach the right node through `lark-thread`.
