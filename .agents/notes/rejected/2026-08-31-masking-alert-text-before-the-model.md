---
title: Masking alert text before the model — declined, with the trigger that flips it
status: rejected
date: 2026-08-31
scope: stack
---

## Decision

Do not add reversible identifier masking (opensre's "mask pod/cluster/account
IDs before the LLM call, restore after") to the judge or the investigator now.

## Why the idea is real

Both services send alert text to a model over a provider's API. The estate
guard keeps real identifiers out of the git REPOSITORY; it does nothing about
what crosses to the inference endpoint at runtime. On a deployment using a
third-party model relay, that is real names, hosts and amounts leaving the
building on every judgement. opensre masks them reversibly and restores in the
reply; CISRE redacts its agent trace to the same end. It is sound engineering.

## Why not now

1. **It fights the product on the investigator.** The judge could tolerate
   masking — it reads a title and a body and rates them. The investigator
   RUNS TOOLS against the real environment: it greps logs for the service
   name, queries metrics by resource id. A masked prompt whose restore table
   the agent cannot see produces an agent that investigates `SERVICE_7` and
   cannot correlate it with anything it queries. Masking the one service whose
   whole job is touching real infrastructure removes the job.

2. **The threat it counters is not this deployment's.** The model endpoint
   here is the operator's own chosen relay, carrying their own alerts about
   their own infrastructure — the same trust boundary as the ledger the alerts
   already sit in. Masking defends against an UNTRUSTED model provider, and if
   the provider is untrusted the credentials and tool output flowing through
   the investigator are the larger leak, which masking the prompt does not
   touch.

3. **The cheaper 80% is already there for the judge.** `HOOKJUDGE_AI_BODY_LIMIT`
   caps how much body text is sent, and the verdict schema pulls specific
   fields — the judge already sends less than the whole alert. Field-level
   selection beats character masking for the same goal without a restore table
   to get wrong.

## Consequences

Declining leaves runtime alert text flowing to the configured model endpoint
unmasked, which is acceptable exactly as long as that endpoint is the
operator's own trust boundary — the assumption is now written down where a
future deployment with a different provider will trip over it. The judge's
existing body-limit and field selection stay the pragmatic mitigation. No code
changes; the survey's one privacy idea is recorded as understood-and-declined
rather than silently skipped.

## What would change the answer

A deployment pointing the models at a provider the operator does NOT control —
a shared or public endpoint — AND a regulatory reason the alert text itself is
sensitive (PII in bodies, not just infra names). Then masking earns its
restore-table complexity, and the place to add it is the judge first (it can
afford a masked prompt) and the investigator only with the restore table
exposed to the agent so its tool calls still resolve. Until both halves are
true, this is machinery defending a boundary this deployment does not have.
