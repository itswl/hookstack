---
title: Assume nobody answers — infer what a ruling would have said, and label it as an inference
status: implemented
date: 2026-08-20
scope: hookjudge
---

## Decision

The attention ledger stops depending on a human pressing anything. It gains a
second, computed signal: pair each firing with the recovery that followed it,
and report how often a condition heals itself and how fast. A condition whose
median self-heal is under ten minutes, more often than not, is reported as
`likely_flapping`.

It is kept in its own keys — `self_resolved`, `median_seconds`,
`likely_flapping`, and a count on the summary — and it never touches `mattered`,
`did_not_matter` or `mattered_pct`. Those still mean "a person said so" and stay
empty until one does.

## Why

The operator said it plainly: **most likely nobody will give feedback.** That is
not a gap to close with better UX; it is the premise the design has to hold up
under, and until now it did not. `ruled 0` on production meant the whole
attention half of this ledger was uncalibrated, permanently.

The mistake underneath it was mine, and worth naming: I had been treating all the
human gates in this family as one kind of thing. They are two, and they fail in
opposite directions.

  A SECURITY gate — writing a skill, writing memory, running a remediation.
    Nobody approves, nothing is written. Failure mode: safe. The investigator
    stops compounding, which is a real cost and an acceptable one.
  A DATA gate — "was this interruption worth it".
    Nobody answers, no data exists. Failure mode: pure loss, and no safety
    bought at all.

Keeping the second one gated was not caution, it was a category error. So the
security gates stay exactly as they were, and the data gate is replaced by
inference.

**The signal was measured before it was built.** On 41 hours of production
traffic, two conditions accounted for over half of all judgements —
`示例充值超500告警` fired 47 times and healed itself 29 times with a median of
5.0 minutes, `示例提现超500告警` 27 and 13 at the same 5.0 — while
`DatasourceNoData` fired 17 times and never recovered once. Identical on the cost
figures. Opposite here. A median that lands exactly on the alert's own evaluation
window is what a threshold flapping looks like, and it is visible with nobody
saying a word.

## Consequences

- `mattered_pct` still returns None until somebody rules, and the noisiest list
  still shows `mattered: 0` beside `likely_flapping: true`. That juxtaposition is
  deliberate: it reads as "the evidence says flapping, no human has confirmed
  it", which is exactly the epistemic state.
- A proxy is weaker than a ruling and the code says so in as many words. A real
  incident can resolve in four minutes; this points at where to look, it does not
  decide. Presenting it as a verdict would be the ledger claiming somebody spoke
  when nobody did — the same class of lie this repository spent a week removing
  from its own comments.
- The pairing is a readable loop rather than a window function, because whoever
  doubts the number has to be able to read it. One edge case is load-bearing and
  tested: a restatement arriving before the recovery must not restart the clock,
  or a storm would appear to have healed in the gap between its last two cards.
- `hookjudge_likely_flapping` joins `/metrics`, so the line that still moves on an
  unattended deployment is the one Prometheus can graph.
- A new Friday patrol (`hookprobe/examples/patrols/self-review.md`) reads both
  numbers back. It is a nudge in Hermes Agent's sense — a system periodically
  prompting itself to consolidate — with this family's gate on the end: it
  proposes at most one memory line and writes nothing. It is written to be worth
  running even if its output is never read, and it is told to report the pile-up
  of unaccepted proposals, which is the one thing nobody else will notice.

## Rejected

- **Auto-accepting memory or skill proposals when nobody responds.** This is the
  tempting version of "assume nobody answers" and it is the one that closes the
  prompt-injection path: an injected line in an alert payload becomes a runbook
  loaded by every later run, reading back as the operator's own. The security
  gates exist for a threat that does not care whether anyone is watching.
- **Counting a self-heal as `did_not_matter`.** One column, two meanings, and the
  cheaper one wins every argument later. Separate keys cost nothing.
- **Nagging.** A reminder card, an unread-queue digest. The operator has said
  they will not answer; sending more things to not answer is not a design.
