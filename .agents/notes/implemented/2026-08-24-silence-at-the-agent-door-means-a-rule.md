---
title: Silence at the agent door means a rule, and only a person can claim exemption
status: implemented
date: 2026-08-24
scope: stack
---

## Decision

`HOOKPROBE_BUDGET_GATES_AGENT_DOOR` arms the meter on `/hooks/agent`. Once armed
and once the window is spent, a request is refused **unless the caller says it is
a person** — `X-Operator: true`, or `"operator": true` in the body. Absence means
automated.

On WebhookWise's side the mirror image: the forward rule that posts
investigations declares nothing, and `run_deep_analysis` — the one layer both the
manual POST and the manual retry pass through, and which the automated forward
path never calls — sets `operator=True`.

Two kinds of ruling now coexist in hookprobe, so the human-facing write path
files under `POST /v1/runs/rulings`, not `/v1/rulings`. `hookprobe.rulings` is
what the investigator concludes about a CONDITION, files with the judge, and has
teeth: a standing not_worth_it answers repeats from the runbook at $0. A run
ruling is what a person concludes about one finished investigation; it gates
nothing and only fills the worth column of the budget report.

## Why

The door used to be the discriminator: `/hooks/webhook` was the automated one and
`/hooks/agent` was where a person asked. That stopped being true the moment a
WebhookWise forward RULE started posting to the agent door — which is how the
family loop has worked for weeks — so the breaker guarded a door nobody
automated was using.

The direction of the default is the whole decision, and it was settled the wrong
way first. The first implementation treated silence as a person: refuse only
callers that declare themselves automated. It reads kinder and it is worse. An
unmarked caller under that rule spends freely, so a forgotten header silently
un-caps the budget the operator just armed — the failure is invisible and costs
money. Under the shipped direction a forgotten header costs one answer, and the
person who was refused can add the header and ask again. A spent budget cannot be
un-spent.

It also matters that the setting already shipped upstream with door-level
semantics and a test named
`test_agent_door_refuses_over_budget_only_when_told_to`. Treating silence as
automated keeps that behaviour exactly as documented and adds an exemption on
top; the first direction would have quietly changed what an existing, tested,
deployed setting means.

A refusal settles as a report-shaped run through the family loop rather than an
HTTP error, so the overrun shows up on the boards instead of disappearing into a
caller's retry. Sessions already in flight stay reachable — a refusal must not
strand a conversation that was already paid for.

## Consequences

The breaker now actually fires on the traffic it was written for: WebhookWise's
rule-driven investigations, roughly the whole of hookprobe's spend. A person is
never blocked from asking, which was the reason the gate stayed unarmed.

The named new failure surface: an automated integration that legitimately wants
to be treated as operator-driven has to say so, and one that forgets stops
working once the window is spent. That is the intended asymmetry, not a bug to
soften — but it means adding a new caller to the agent door is now a decision
about which side of the meter it sits on.

## What would change the answer

- A third kind of caller that is neither a rule nor a person (a chat surface, a
  scheduled patrol a human asked for) needing per-caller budgets rather than one
  bit. Then the bit becomes a small policy table, not a wider exemption.
- Refusals landing on real operator traffic. The ledger records every refusal
  with its cause, so the number is readable; if people are being refused, the
  header is missing somewhere upstream and the fix is there, not here.
