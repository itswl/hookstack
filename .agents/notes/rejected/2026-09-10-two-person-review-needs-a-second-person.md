---
title: Two-person review, freeze windows and the ladder to Zero-Touch need somebody to be there
status: rejected
date: 2026-09-10
scope: stack
---

## Decision

From a write-safety review of the read-write nodes, these are declined for this
family. They are on every industry checklist, so they will be proposed again;
this is why they were not taken here.

* **Synchronous two-person approval** (Maker–Checker: a high-risk card requires
  an SRE and a service owner to press separately).
* **Calendar freeze windows** that grey out the approval button during
  promotions, earnings days and 00:00–06:00.
* **The trust ladder whose top rung is progressive unattended self-healing.**
* **gVisor / Kata as specified**, and **eBPF that `SIGKILL`s**, both rejected as
  written rather than on merit.

## Why

**Nobody answers.** This is the constraint the whole family is built around and
it is already written down in [[automation-graduates-on-its-record]]: *"nobody
here answers in real time, so sampling has to be asynchronous or it will not
exist."* Single-approver cards already sit unpressed — the production ledger
reads 5 proposed, 0 approved, 0 dismissed for remediation. Requiring two
specific role-holders to press does not make a high-risk action safer; it makes
it never execute. If never-execute is the intent, the honest implementation is
to leave the pattern out of the allowlist, which costs no code and does not
advertise a control that has never fired. The mechanism this family uses
instead is asynchronous: a per-class tier ceiling plus an after-the-fact
sampling review, which is the shape a rota-less deployment can actually run.

**Freeze windows cut on the wrong axis.** hookstack already refuses stale
approvals twice — the 24h approval window, and the freshness cursor, which
retires a procedure when its condition has ended or the investigation has taken
another turn since the steps were chosen. Those key on *evidence*, and a
calendar keys on *the clock*. A correct action at 02:00 against live evidence
carries less risk than a stale one at 15:00, so a rule that permits the second
and forbids the first has the axes crossed. A change-freeze belongs to the
enterprise release system the deployment sits inside, not to the approval
button.

**Nothing here graduates itself, by design.** The ladder as drawn assumes trust
rises with time and terminates in unattended execution. The equivalent already
built is deliberately not that: a tier is a *per-class ceiling* an operator
moves by diffing config, `supports()` and `review()` are advisory, and no code
path promotes anything. And the top rung was settled two notes ago —
[[remediation-runs-behind-two-gates]] closes with *"Revisit if a class of
remediation needs to run without a human (it should not, given the blast
radius); that would be a new note, not a flag flip here."* Reviving it needs an
argument against that sentence, not a pyramid diagram.

**gVisor as specified names a Kubernetes field this stack does not have.**
`runtimeClassName: gvisor` presumes pods; this is four containers on one host
under compose. The idea is not wrong — a compose-level `runtime: runsc` is a
real option — but it is a different proposal, and it owes a measurement first:
these containers run node and python CLI subprocesses over heavy file IO, which
is the workload shape runsc taxes hardest.

**eBPF observation yes, `SIGKILL` no.** Kernel-level syscall visibility is the
one thing that would cover what the bash guard openly cannot — a python
one-liner opening its own socket reads none of the proxy variables and matches
no pattern, and that limit is asserted as a test so nobody reads the rule as
stronger than it is. But killing the process is the wrong response *here*: the
refusal log is the product. A kill destroys a paid run and leaves no readable
account of what was attempted, which is the opposite of what this deployment
exists to produce. Alert and audit; reserve the kill for escape-class events,
where there is nothing to preserve.

## Consequences

* The next reviewer arriving with the same standard checklist finds the reasons
  here instead of re-deriving them, which is the entire point of this bucket.
* **Two of these reverse the day hookstack is attended.** If a staffed rota ever
  exists, two-person review on high-risk classes becomes reasonable and this
  note gets superseded. The trigger is a rota, not a larger blast radius —
  a bigger consequence with nobody watching argues for a narrower allowlist,
  not for a second approver who is also not there.
* gVisor and eBPF-as-observation stay open. Either could be proposed properly:
  gVisor with an overhead measurement on this workload, eBPF as a detection
  feed into the audit rather than an enforcement point.
* What survived the same review, and is worth building, is in
  [[three-gaps-before-the-execution-door-opens]].
