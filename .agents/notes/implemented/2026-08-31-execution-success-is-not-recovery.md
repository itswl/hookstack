---
title: Execution success is not recovery — a verification contract for remediation
status: implemented
date: 2026-08-31
scope: hookprobe
---

## Decision

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

**Landed 2026-09-28, sized as below with three differences the loop taught in
the meantime.** `remediation.outcome` is the one function that says what the
world said about an executed procedure — `held`, `did_not_hold`, `verifying` —
and `work.py` verifies only on `held`. The stamp is written by
`remediation.evidence` from two doors: the recovery door holds a procedure
whenever the condition ends (inside the window or after — evidence is
evidence), and the event door's re-fire path marks one `did_not_hold` when the
same alert fires again inside `HOOKPROBE_REMEDIATION_VERIFY_SECONDS` (default
3600, 0 off). The first answer stands. A window that closes quietly reads
"no re-fire within the window — thinner than a target re-read, and said so",
which is the wording proposed below kept to the letter. The audit gets a
`tool: Outcome` line beside the `Exec` lines; the console and the board show
the outcome beside the approval and beside who approved it (`approved_by`,
landed the same day). Rows executed before the window existed carry none and
are not accused; a recovery stamped on their run still holds them.

The trigger written below — the first real proposal — fired on 2026-09-07 with
five proposals for one condition, none approvable
([[pilot-zero-read-back-and-the-order-of-the-next-ninety-days]]); the contract
lands now, before the first real execution, because the quickstart's rehearsal
executes a procedure on every `demo.sh` and the smoke asserts the outcome.

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

## Told, 2026-09-28

The verdict now travels: a recovery, a re-fire or the window closing posts one
notice through the investigator's return door — `<alert> · fix held` / `· fix
did not hold` — so it reaches the chat thread and the pipe's journey of the
alert. See
[the-verdict-on-a-fix-is-told-where-the-report-went](2026-09-28-the-verdict-on-a-fix-is-told-where-the-report-went.md).
