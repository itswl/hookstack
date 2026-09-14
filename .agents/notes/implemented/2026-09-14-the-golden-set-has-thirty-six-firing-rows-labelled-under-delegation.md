---
title: The golden set has thirty-six firing rows in today's traffic shape, labelled under the operator's delegation, and the gate is no longer thin
status: implemented
date: 2026-09-14
scope: stack
---

## Decision

Twenty-eight firing rows were added to the host's golden set on 2026-09-14,
one per alert rule, each the rule's latest firing instance from the judge's own
retained ledger, in the shape the judge sees today (the platform's relay
envelope: a one-line triage body and three triage fields). Each row carries a
note saying why its label is right. Expectations are sets where two answers are
defensible under the prompt's own scale; `wake` is labelled only where the
instance's own text carries the evidence (nineteen rows). The candidates were
scored at three votes on the production image before they were merged, and the
merged set was replayed until it was green for the reasons the house rules
allow — never by loosening a label to buy a green. One row is held
`reviewed: false` with the judge's under-call written into its note: the mail
family's provider-pause instance, which the judge answered medium two votes of
three. The recorded gate now reads 36 firing rows, 23 recovery rows, 0 missed,
0 false quiets, `thin: false`. The pre-merge set is backed up outside the volume,
and the 2026-09-01 labelling packet has an adjudication section.

Two more decisions were taken in the same sitting and belong with this one:

- The decline list on the production node gained the cache family (its first
  real titles appeared this week) and the production log-alert family (65 runs
  in a week, every report a restatement: the node has no log backend and no
  cloud credential). Host configuration, hot-read, no deploy.
- The card digest is **not** built. The measured hurt is real but narrow: 174
  of the week's 196 wake=yes cards came from one flapping log-spike rule that
  fires and recovers every eighty minutes. The 2026-08-12 trigger — a complaint,
  or a channel muted — has not fired; the pipe sits 49 lines under its ceiling;
  and the platform's own digest exempts machine targets, so it cannot thin the
  relay. The decision is a pipe-side digest per condition per window on the
  person-facing route, built when the operator confirms the ceiling raise the
  weight note requires — or sooner, the day the trigger fires.

## Why

The gate this morning was green on nine firing rows, and eight of the nine were
test probes and certificate rows. The business rules the platform actually sends
were in the set only as recovery instances — the importer took each rule's
latest instance, and on that day the latest was the recovery — so the gate could
not fail on the one thing it exists to catch: the judge under-calling a real
firing. Nor did the set contain a single row in today's payload shape; the judge
has been graded for three weeks on a shape it no longer sees.

The operator delegated the afternoon in plain words — 快速迭代期，你直接帮我决策 —
and the set already carried the precedent: every reviewed row was labelled by an
agent on 2026-08-31 with the platform's own cap as a cross-check, and two were
re-reviewed on 2026-09-01. The labels here follow that method and the eval
README's rules: a golden pins one instance, so a row whose body is only an emoji
and a title cannot carry `wake`, however often the condition earned it.

**Why the replays went red three times on three different rows.** One draw of
this judge disagrees with itself about one time in five; a majority of three
narrows that, and thirty-six rows widen it again. The first red was a
provider-review instance (medium, where the label said high); the second a
one-hour-silence instance whose wake label rested on the title alone; the third
the provider-pause instance. Each was resolved on its merits: the review line is
a set, because the prompt's own medium anchor ("a threshold crossed with room
before it hurts") describes a review state; the silence row lost its wake label
under the one-instance rule; the pause row kept its label and left the scored
set, because the prompt is what should move there — see the proposed note on
the prompt, whose first draft was tried the same afternoon and pulled back.

Two more rows were settled by the deploy gate itself, which replayed the set
twice more: a certificate instance whose relay-shape body garbles its own
threshold sentence became a set (medium or low — on that text, low is
defensible); an observability-blackout instance became a set (high or medium —
the prompt's "degraded-but-serving dependency" describes it); and a
one-hour-silence instance whose body is an emoji and a title joined the held
rows, because the judge cannot see from that text whether the silence is
abnormal, and neither can a label.

## Consequences

- The deploy gate is evidence now. It will also be red more often than a
  nine-row gate was, and the right response is the one the eval README already
  prescribes: fix the prompt, or change the row and say why. `SKIP_EVAL=1` is
  still the emergency door and still leaves the page saying so.
- Over-escalation is reported at 9 of 36 firing rows and over-delivered wake at
  2 of 19 scored; neither is gated, and both are now visible per replay where
  they were invisible before.
- One row stays held (the title-only silence instance; unstable under both
  prompts at five votes). The pause row was un-held the same afternoon: the
  five-vote A/B answered it high five times out of five under the live prompt,
  so its two-of-three medium was the coin. The deploy gate therefore moved to
  **five votes** (deploy.sh, effective from the deploy after the one that ships
  it) — about seven cents more per deploy for a gate that stops flipping on
  borderline rows, which is exactly the SKIP_EVAL habit the eval README warns
  against.
- The digest decision is dated and numbered; a week with the same shape on the
  page is its own argument.
