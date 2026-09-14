---
title: One pending proposal per procedure — a re-fire points at the row already waiting
status: implemented
date: 2026-09-14
scope: hookprobe
---

## Decision

Before parking a report's remediation block as a new proposal, the service asks
`remediation.pending_duplicate` whether a `proposed`, unexpired row already
carries the same procedure. If one does, the run points at it
(`meta.remediation_proposal`), says so (`meta.remediation_proposal_reused`), and
no new row, button or 24h clock is created. The procedure is its commands, as a
sorted tuple (`procedure_key`); labels and targets do not count. Executed,
rejected and expired rows are history, and the same procedure proposed after
them is a fresh question. `propose()` itself is unchanged: it parks what it is
handed, and the policy lives where the run is known.

## Why

Measured on production 2026-09-14: five proposals on disk, all `proposed`, all
past the approval window, all from one condition re-firing every four hours on
2026-09-07/08, and five of their eight steps the character-identical
`aws sesv2 get-account --query SendingEnabled`. Each re-fire's report proposed
the check again; each got its own row, its own approve button on its own card,
and its own 24-hour clock. Nobody pressed any of them — the card-press ledger
reads nine presses ever, none since 2026-08-17 — and the actions page listed
five decisions where there was one.

WebhookWise's remediation proposals have the rule this copies: one pending
proposal per action and resource, a duplicate refused with 409 while the first
waits. The same-condition re-fire is exactly the case that produces duplicates
here, and it now produces more of them: the re-fire answer path delivers a card
per re-fire at $0, and a card whose report carries a remediation block would
have parked a row per card.

**Why commands and not targets.** The target cooldown learned this on
2026-09-10: the model labelled one target three ways (`AWS SES 账户状态`,
`AWS SES account status`, `AWS SES 账户状态（只读）`) while the command it
wrote stayed identical. The label is what a person reads; the command is what
holds, so the command is the key.

**Why the run points at the existing row rather than at nothing.** The board
groups proposals by the session that parked them, so the earlier work item keeps
its `waiting_approval` and the re-fire's item shows no second one — which is the
truth, one procedure is waiting. The pointer on the later run is for the reader
of that run: the report proposed something, and the answer to "where did it go"
is a row id, not silence.

**Why history does not count.** A procedure executed yesterday and proposed
again today is a new decision — the condition came back after the fix — and the
operator should see a new button. Likewise a rejected or expired row: somebody
said no, or nobody came, and a re-fire after that is a fresh question with the
freshness cursor of its own run.

## Consequences

- The pending row keeps its own cursor (its session's `run_id`, its recovery
  flag). A later re-fire does not move it, so it stays approvable inside its
  window from any card that points at it, and the freshness check at approval
  is unchanged.
- The automation ledger records one `proposed` row per procedure, not one per
  re-fire, so the graduation figures stop counting the same proposal five times.
- Two proposals whose commands differ by a flag are two procedures. That is the
  cooldown's blind spot too, stated in its containment row; a model that varies
  a command across re-fires is not deduplicated here either.
- `test_the_list_says_which_proposals_are_past_their_window` still parks two
  identical procedures through `propose()` directly — the primitive still does,
  and the policy is the service's.
