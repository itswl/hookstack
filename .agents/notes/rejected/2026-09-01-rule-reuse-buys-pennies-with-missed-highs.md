---
title: Rule-wide verdict reuse — rejected, the ledger replay prices it
status: rejected
date: 2026-09-01
scope: hookjudge
---

## Decision

`HOOKJUDGE_RULE_REUSE_WINDOW_SECONDS` stays 0. Identity-level reuse
(`HOOKJUDGE_REUSE_WINDOW_SECONDS=3600`) stays as is.

## Why

The question waited on labels; the answer came cheaper — an offline walk of
both shadow judges' own 30-day ledgers (560/521 non-recovery rows, 436/414 of
them ai-route), simulating anchor-based rule-wide reuse at four window widths
and counting two things: ai calls that would have been skipped, and skipped
rows whose ACTUAL verdict differed from the one they would have inherited.

| window | saved ai calls (A/B) | inherited-lower-than-actual (A/B) |
| --- | --- | --- |
| 5 min | 0 / 0 | 0 / 0 |
| 15 min | 4 / 2 | 0 / 0 |
| 1 h | 2 / 1 | 0 / 0 |
| 4 h | 81 / 74 (~18%) | **5 / 6 — all on the 充值/提现 payment rules** |

Small windows save nothing because identity-level reuse already absorbs
restatements (107/89 reuse-route rows). The first window that saves real calls
buys them exactly where under-calling is most expensive: payment rules whose
instances legitimately vary in severity within the window. And the money at
stake is ~$0.46/month at measured GLM prices ($0.0342 per 32 verdicts) — there
is no invoice pressure to spend missed highs on.

## Consequences

Every alert instance of a rule keeps paying for its own judgement, which is
the point. Revisit trigger: call volume or per-verdict price grows ~100×, or
the payment rules leave the estate — then re-run the same simulation (one
read-only sqlite walk over `judgements`, anchor per rule, count saved vs
inherited-lower) before touching the setting.
