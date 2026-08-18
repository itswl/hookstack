---
title: Remediation executes behind an operator's click and an allowlist, never the agent
status: implemented
date: 2026-08-18
scope: hookprobe
---

## Decision

The investigator stays read-only. A report MAY append a fenced `remediation`
block; the service lifts it into a proposal, and the proposal executes only
after an operator approves it AND every step's command full-matches a regex in
`HOOKPROBE_REMEDIATION_ALLOWLIST`. No allowlist, no execution — which is the
shipping default. The agent is never in the execution loop.

## Why

"Read-only investigator" and "closes the loop" sound opposed; they are not, if
the writing hand is never the agent's. The agent proposes in words (its report,
which a human reads anyway); the service — the same code that already owns the
input guard and the audit log — is what runs an approved, allowlisted command.

Three gates, each answering a different failure:

- **The allowlist** (deny-by-default, full-match) answers "what class of
  command may ever run here". It is hot-read, so tightening it needs no
  restart, and full-match not search so an allowlisted prefix cannot license a
  `; rm -rf /` suffix. A broken pattern fails closed.
- **Approval** answers "should THIS instance run now". Checked against the
  allowlist for EVERY step before any step runs: a procedure written 1-2-3 that
  half-executes is the worst outcome, so approval is all-or-refused.
- **Sequential, stop-on-failure execution** answers "what if step 2 breaks" —
  step 3 does not run, and the row is `failed` with the output of the step that
  broke.

The read-only bash guard's deny list deliberately does NOT apply to the
executor: remediation exists to perform the mutations that guard blocks, and
gating it on that same regex would mean it could never do its job. Its gate is
the operator's file plus the operator's click.

The proposals directory is on the input guard's protected list, so a run
cannot forge a proposal's provenance — and even a bash write around the guard
yields only a `proposed` row, which approval and the allowlist still stand in
front of.

## Consequences

- Default posture is collect-only: without the allowlist the actions page fills
  with proposals and nothing runs, which is a safe thing to ship on by default.
- Execution is `create_subprocess_shell` in the container, so its blast radius
  is the container's — bounded by the same limits (cap_drop, pids, mem) and the
  same mounted read-only credentials as everything else here.
- Rollback is advisory text on the proposal, not automation. If a step needs a
  guaranteed undo, that undo is itself a remediation an operator approves.
- Revisit if a class of remediation needs to run without a human (it should
  not, given the blast radius); that would be a new note, not a flag flip here.
