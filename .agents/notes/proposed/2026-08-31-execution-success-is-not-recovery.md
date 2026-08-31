---
title: Execution success is not recovery — a verification contract for remediation
status: proposed
date: 2026-08-31
scope: hookprobe
---

## Decision proposed

When a remediation proposal is approved and its steps run, the loop must not
close on exit codes. Borrowed from CISRE (William-Lu-stack/Flawless), whose
closed loop is the most serious safety engineering in the 2026 AI-SRE field
survey: "execution API success, model claims, or the old instance remaining
healthy do not equal recovery — only new evidence from the real target
satisfying a recovery contract closes the loop."

Our remediation path today ends one step earlier: propose → approve →
allowlist → execute sequentially, stop-on-failure, each step audited — and
then nothing asks whether the CONDITION cleared. A remediation that ran
cleanly and fixed nothing looks identical to one that worked.

## The shape (deliberately sized to this stack)

Not CISRE's typed-action schema and verifier plugins — at our scale the
family loop already carries the evidence needed:

1. When a proposal executes for a run whose meta names condition X, the
   service records `verifying_until = now + N` (default 60m) on the proposal.
2. The judge already sees every re-fire of X. A firing of X inside the window
   marks the proposal `did_not_hold`, with the firing's correlation_id; the
   window expiring quietly marks it `held (no re-fire within N)` — phrased
   exactly that way, because absence of a re-fire is weaker evidence than a
   target re-read and the record must not claim more than it knows.
3. The actions board and the audit line show the outcome beside the approval,
   so "who approved what" is completed by "and did it work".

Also worth taking when this lands: per-runbook effectiveness (a runbook whose
remediations repeatedly did_not_hold is a runbook whose procedure is wrong),
and upgrading the allowlist toward typed actions if proposal volume ever
justifies schema work.

## Why not now

Zero remediation proposals have ever been filed on this deployment — the
agent-proposes convention shipped 2026-08-21 and nothing has used it. Building
verification for a path with no traffic joins the preflight check and the
critic pass in the same parked queue, all three sharing one trigger:

**Trigger: the first real remediation proposal appears on the actions board.**
Then preflight (allowlist pre-match at park time), critic (one review pass
before parking), and this verification contract land together — they are one
feature seen from three sides: is it safe to run, is it right to run, did it
work.

## Consequences

If adopted at trigger time: the remediation loop gains the property the rest
of the stack already has — every claim carries a counter somebody can check.
If proposals stay at zero for months, that is its own verdict on the
remediation feature, and the honest move is questioning the feature rather
than decorating it.

## What would change the answer

A remediation class whose recovery a re-fire cannot witness (a config change
that silently degrades instead of re-firing). That needs CISRE's stronger
form — an explicit target re-read per runbook — and would be the moment to
copy their recovery-contract idea properly rather than the cheap proxy.
