---
title: A green suite is not a tested guard — break each claim and require a test to notice
status: implemented
date: 2026-09-10
scope: stack
---

## Decision

`scripts/assert_guards_are_tested.py`, on the gate. It takes twelve containment
claims, breaks each one on purpose — deletes the read-only rule table, empties
the withheld-secrets list, makes the MCP gate return `None`, drops the audit
chain's link, widens the agent's bearer to writes, makes the approval window
infinite — runs only the tests that claim to protect that claim, and requires
them to **fail**.

Twelve mutations, twelve caught, 9.2 seconds.

## Why

A green suite proves the code passes its tests. It does not prove the tests
would catch the code being **wrong**, and that is this repository's recurring
bug shape rather than a hypothetical: a boundary described in `containment.md`,
reported by `/v1/agent`, and absent in fact.

The same argument has been made twice before at a different level, which is what
makes this the obvious third:

* `gate.verify` — a gate that could not answer looked identical to one that did,
  so the node hands it a call no posture permits before trusting a turn to it.
* `/v1/selftest` — the surfaces all described boundaries, so the node performs
  them instead.
* And the gate already carries one inverted check: `assert_node_contract` must
  FAIL on a round it was written to catch, "because a checker that has quietly
  stopped catching anything looks exactly like one with nothing to catch".

This is that sentence aimed at the suite. It was written after checking by hand
that today's new guards were not decorative, and the honest next step was to
make the check repeatable rather than to report a one-off result.

**What it is not, stated in the file so nobody oversells it:** not mutation
coverage. Seven modules and a hand-written list is a spot check on the claims
that would hurt most if their tests were ornamental, not a proof about the
suite. The ceremony is one entry per new boundary.

**Why it is cheap enough to be on the gate.** Each mutation runs only the test
files that claim to protect it, not the suite — 9.2s for all twelve, against
minutes for the docker builds already there. A check nobody runs protects
nothing, and this repository has said so about its own steps; putting it behind
a "run it deliberately" README line would have made it decoration.

## Consequences

* **An entry that rots is a failure, not a skip.** If its anchor text no longer
  appears exactly once the run fails with "this entry has rotted" — the
  alternative, silently skipping, is the same green-that-means-nothing this
  exists to prevent.
* Adding a containment row without adding a mutation leaves a claim whose test
  nobody has questioned. `containment.md` now says which rows are covered, so
  the gap is visible rather than assumed.
* It restores every file from a copy in a temporary directory, in a `finally`,
  so an interrupted run cannot leave a mutated tree. Verified by running it
  against a clean tree and checking `git status` after.
* Twelve of twenty-four boundaries. The rest are config, network topology or
  cross-service, and are not mutable by editing one Python file — naming that
  here so the number is not read as coverage.
