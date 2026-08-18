---
title: The root gate delegates to component gates; it never replicates a check
status: implemented
date: 2026-08-18
scope: stack
---

## Decision

`scripts/gate.sh` at the repo root runs each component's own
`scripts/gate.sh` (all three by default, or the ones named as arguments),
then the three stack checks no component covers (check-docs, assert_design,
assert_agent_notes). It contains no check of its own.

## Why

Two pushes went red on the same day for the same shape of reason: a
cross-component change ran one component's pytest and nobody ran the other
component's gate — `ruff format --check` and mypy only existed in CI's view
of the change. The gap was never a missing check; every component already
carries an exact CI replica pinned by its own `test_gate_matches_ci`. The
gap was a missing single entry point for "I touched more than one thing."

Replicating the check list at the root — the first draft did — would have
created a second copy of every component's contract with CI, one that no
`test_gate_matches_ci` pins, drifting silently until it blesses what CI
rejects. Delegation keeps one source of truth per component.

## Consequences

- "Before every push" is one command from the repo root, regardless of how
  many components the change touched.
- The stack trio (docs/design/notes) finally has a local runner; it was
  CI-only before.
- Component gates keep their own venvs and their own contracts; the wrapper
  adds nothing they must stay in sync with.

## Rejected

- A root gate with its own flattened check list: exact today, unpinned
  drift tomorrow.
- Adding stack-smoke to the wrapper: it boots the whole family under docker
  compose and belongs to CI, same as the docker-build jobs.
