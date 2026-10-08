---
title: An approval names what was read — a press carries the proposal's digest
status: implemented
date: 2026-10-08
scope: hookprobe
---

## Decision

A press approves a VERSION of a proposal, not an id. `remediation.content_hash`
is a sha256 over the canonical JSON of the fields that decide what runs and
whether it may: `id`, `session_key`, `created_at`, `steps`, `cursor`. Not the
fields the lifecycle rewrites (`status`, `results`, `approved_*`,
`resolved_at`), which change under a proposal nobody touched.

Both doors carry it:

- **the card**: the investigator's `approve` declaration now has a `hash`
  beside its `ref`, taken from the same row as the command the button names.
  The pipe already signs every declared key except the text into the button's
  token, so the digest is tamper-proof in the chat and comes back with the
  press; no change in hookrelay.
- **the console**: `GET /v1/remediations` gives every row its digest, both
  pages that draw an approve button keep it beside the steps they drew, and
  `POST /v1/remediations/{id}/approve` reads `{hash}`.

`remediation.approve` requires it and checks it first, before the window and
the cursor, because both of those are read from the row and a rewritten row
would answer them about itself. A press naming no version, or another one,
raises `Changed`: nothing runs, the row is left exactly as it is — not retired,
because nothing about the condition is known to have moved — and on the card
path the refusal goes back to the chat as a report, the way a moved condition's
does, because the bridge has already repainted the card. The console redraws the
page on a refusal so the reader sees the proposal as it now is. The approved
digest goes on the row as `approved_hash`.

## Why

The porting table of [[pilot-zero-read-back-and-the-order-of-the-next-ninety-days]]
listed it for step 3, from the successor project that built the same loop: an
approval binds to the version and hash of what was read. Here a press named
only an id, and the module that holds proposals concedes that a bash write
around the input guard can still produce or rewrite a row. Between the card
being read and the button being pressed, anything that rewrote the file under
that id would have run as approved by somebody who never saw it. The window and
the cursor cannot catch that: they are fields of the same file.

The operator asked for it on 2026-10-08, after reading the assessment that the
work should move from defence depth to the loop on the phone — this being one
of the two defences the phone press needs to be trusted (the other, who
pressed, landed 2026-09-28).

## Consequences

- Execution was already safe from a rewrite AFTER the click: the executor runs
  the steps of the row `approve` returned, in memory, never re-read from disk.
- Mandatory, not optional: an optional digest is skipped by the one caller that
  should not skip it. Every test that approved by id alone now approves the way
  a person does (`tests/helpers.approve_as_read`).
- A card minted before this change carries no digest and is refused with "does
  not say which version"; no deployment draws approve buttons on cards today,
  so none exists in a chat.
- `scripts/assert_guards_are_tested.py` breaks the comparison on purpose and
  requires `test_remediation.py` and `test_card_actions.py` to notice;
  `docs/containment.md` carries it in the approval-window row.
- What it does not do: it is not a signature over the steps and it does not
  stop a person approving a rewritten proposal after reading it. The digest
  makes the approval honest about what it was of; whether the steps should run
  stays the allowlist's question.
