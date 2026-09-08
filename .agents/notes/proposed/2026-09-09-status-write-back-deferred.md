---
title: Status write-back — deferred, with the target picked and the three decisions it needs
status: proposed
date: 2026-09-09
scope: hookrelay
---

## Decision

Not built. The operator deferred it on 2026-09-09 after the shape was agreed, so
this is the parking space rather than a re-derivation next time. **The mechanism
is already in place**: a channel can state the method its receiver wants and
carry that receiver's own credential (`options.method`, `options.headers`,
committed 2026-09-08). What is missing is a target, a credential, and three
decisions only the operator can make.

## Why

The gap it closes is one-directional knowledge. The work deployment's planner
**already reads Jira** — the audit record shows it pulling `SRE-*` issues for
status and changelog — so the work genuinely lives in a ticket, and the ticket
has no idea hookstack ever looked at it. Somebody opening that ticket tomorrow
re-does the investigation or ignores it; the conclusion is in a chat scroll and
the ticket is in Jira and nothing links them.

The pattern is not new here. `to-judge-feedback` is already a write-back: when a
person presses a card button, the pipe writes that fact back to the judge's
`/feedback` door, which is how a ruling reaches the thing that produced the
verdict. The external version is the same shape pointed outward.

## The shape agreed, so it does not have to be argued again

- **Target: Jira comments on the work deployment**, not the alert side.
  WebhookWise's MCP surface is read tools with no place to attach an analysis,
  and the person who would act on an alert already has the card.
- **Content: one line and a session key, not the report.** It avoids agreeing a
  payload shape with another system, and it keeps an investigation's text from
  being copied into a second store.
- **No link, for now.** The obvious thing to write back is the audit page URL,
  and `HOOKRELAY_PUBLIC_URL` is loopback on both deployments — a pointer to a
  page nobody can open is worse than no pointer. This unblocks itself the day
  there is a reachable address.
- **The pipe writes, not the agent.** Through a channel, so every write-back is
  a row in the delivery ledger with retries, dead-letters and a rate limit —
  and so the agent stays read-only, which is the product's central claim.
- **Words automatically, state only with a person.** A comment is low risk and
  must be automatic or nobody will approve forty-four of them a week.
  Transitioning or closing a ticket goes through the same signed approval and
  allowlist as a remediation, if it is ever wanted at all.

## What it needs before it can start

1. A Jira credential that can **only comment** — not transition, not edit
   fields. The existing token is read-only (verified 2026-09-07) and cannot do
   this, and this would be the first time hookstack holds a write credential
   for somebody else's system.
2. Which tickets should receive one: every issue the planner read, or only the
   ones whose work was handed off and executed.
3. Confirmation that a one-line conclusion may leave the deployment at all.

## Consequences

**A new boundary, when it lands.** The credential goes in the containment table
as its own row with what it does not stop — a comment-only token still lets a
compromised pipe write into every ticket it can see, and that is the honest
sentence to write next to it.

**Retries will duplicate.** The pipe sends `X-Hook-Idempotency-Key` and almost
no external API honours it. The first version should accept an occasional
repeated comment and say so, rather than grow a read-before-write step for a
cosmetic problem.
