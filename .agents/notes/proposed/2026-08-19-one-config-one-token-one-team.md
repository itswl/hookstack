---
title: One config, one token, one team — the adoption ceiling, named
status: proposed
date: 2026-08-19
scope: stack
---

## Decision

Not built. Every deployment of this family serves exactly one audience: one
`config.yaml`, one read token that grants the whole ledger, one admin token that
grants every mutation, one judge ledger, one investigator volume. There is no
scoping of any of it by team, service or environment.

The recommendation, if this is ever wanted, is **one deployment per team** rather
than tenancy inside one deployment — and the reason is worth recording before
somebody starts adding an `owner:` field to routes.

## Why

The components are small and cheap to run: the pipe and the judge are ~275 MB
images with a SQLite file each. Two teams running two deployments cost almost
nothing and get complete isolation of config, ledger, tokens and spend — with no
new concepts in any component.

Tenancy inside one deployment costs a great deal more than it looks. `read_token`
would have to become a set of tokens with scopes; `/status`, `/trace`,
`/metrics`, `/disagreements` and the judge's attention numbers would each need
filtering; the investigator's volume holds case files, distilled skills and an
environment memory that are all deployment-global by design, and splitting them
per tenant undoes the compounding that makes the investigator worth having. The
`card_actions` ledger (2026-08-19) would need to know which tenant a press may
act on. That is four components learning a concept none of them has, to save
running a second container.

What makes this worth a note rather than silence: the pull is real. A read token
that shows every team's alert titles is a genuine objection, and it will be
raised. The answer should be "run your own" and not "we will add scopes",
because the second answer is a rewrite.

## Consequences

- If per-deployment is the answer, the thing worth building is not tenancy but
  **making a second deployment trivial**: the compose files already parameterise
  everything, and the remaining friction is the `card_actions` and channel
  secrets. That is documentation, not code.
- One thing genuinely does not shard well per team: the investigator's learned
  skills. A team-per-deployment split means each one starts cold. The exportable
  form of a distilled runbook (SKILL.md is already a shared format across the
  OpenClaw lineage) is the thing that would make that acceptable, and it is
  useful on its own.
- If tenancy is ever built anyway, the token model is the first domino and the
  hardest to reverse. Decide it before anything else.

## Rejected

- **An `owner:` or `team:` field on routes as a first step.** It reads cheap and
  is not: the moment it exists, every read endpoint either honours it (four
  components change) or leaks past it (worse than not having it).
- **Per-tenant investigator volumes inside one deployment.** It splits exactly
  the asset that gets more valuable by not being split.
