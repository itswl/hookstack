---
title: One process and one SQLite are the shape — three scaling proposals, measured and declined
status: proposed
date: 2026-09-07
scope: stack
---

## Decision

Not built, and not to be built by accident: a multi-backend store behind a
Repository interface, a tool that propagates the pinned copied helpers, and a
priority queue in delivery. An external review proposed all three on 2026-09-07
as "high availability, developer experience, performance". Each was checked
against the code and the production ledger before being declined, and this is
the record, so the next reviewer who reaches the same three (they are the
obvious three) finds the numbers instead of re-deriving the feeling.

## Why

**A store abstraction over SQLite and Postgres.** The pipe and the judge are
single-process by design and say so — `fuse.py` counts in process memory
*because* there is one process, and the pipe's README caps its own size. The
product claims no SLA and no on-call (hookrelay/README.md), and the deployment
this was worn in on carries **858 sent deliveries a week**. A Repository with two
backends is every query written twice, drifting independently, for a deployment
shape nobody runs — the exact class of "second copy" this repository spends its
checks preventing. It would also be the wrong HA: two pipes cannot share one
ledger without giving up the single-writer semantics that make the ledger a
record rather than a race.

**A tool that syncs the pinned copies.** `assert_copies.py` pins twelve helpers
across the three services. Their files have changed between one and five times
in the life of the repository (`live.py`: 1, 1, 2 commits). The checker already
names the exact helper and the exact service that drifted; the fix is a paste.
An AST rewriter that edits three services in one command is more machinery than
the paste it replaces, and it adds the one failure the copies cannot have today:
a bad edit propagated everywhere at once.

**A priority queue in delivery.** The proposal reads the FIFO in
`due_deliveries` (`ORDER BY id LIMIT 50`) and infers that a critical card waits
behind low ones in a storm. Measured on production over seven days: 858 sent
deliveries, event-arrival to sent **p50 0.2 s, p95 0.5 s, max 0.5 s — for
every importance**, zero deliveries over 60 s, and zero minutes in which any
one channel carried more than three deliveries. The storm fuse (per door) and
the judge's reuse route collapse a storm before it reaches delivery; the queue
never holds anything to prioritise. Ordering by importance would also be the
pipe reading a judgement — permitted as "a field the brain set", but there is
nothing to order.

The fourth proposal in the same review — local models via Ollama/vLLM — was not
a proposal: `HOOKJUDGE_AI_BASE_URL` is an OpenAI-compatible base, the dialect
negotiation exists for providers without structured output, and the rule floor
is the backstop. What was missing was the paragraph saying so and a front page
that listed it as future work. Both fixed in the commit that adds this note.

## Consequences

- None of the three is wrong forever; each has a number that would reopen it.
  Reopen the store question when a second team needs the SAME ledger, not when
  load grows — load is answered by a second, independent pipe behind the
  upstream's own fan-out, which keeps every property this family has. Reopen
  the sync tool when a pinned helper changes more than once a month. Reopen the
  priority queue when the measurement above shows a high-importance delivery
  waiting behind others — the query is in the review thread, and takes a
  minute to re-run.
- A review that proposes scaling machinery for a small-team tool is not wrong
  about the machinery; it is wrong about the tool. Answer with the ledger.
- Local models are documented in hookjudge/README.md under "Local and
  self-hosted models". What remains genuinely open there is *measurement*: the
  golden set has never been run against a 7B model, and the front pages now say
  that instead of "integration".

## Rejected

- **Build the store abstraction "while it is small".** It is never smaller than
  now, and it is still two copies of every query for zero deployments.
- **Ship the sync tool as a convenience.** A convenience that can propagate a
  mistake to three services is a hazard with a friendly name.
- **Add priority as a cheap safeguard.** A safeguard against a wait that has
  never exceeded half a second is a comment pretending to be code.
