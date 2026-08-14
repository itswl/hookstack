---
title: A finished investigation drafts a runbook; a person saves it
status: implemented
date: 2026-08-15
scope: hookprobe
---

## Decision

`POST /v1/runs/{key}/distill` returns a SKILL.md draft assembled from a finished
run — the question, the tool sequence, the report's opening conclusion. It writes
nothing. Saving stays `PUT /v1/skills/{name}`, which is a person clicking Save.

## Why

The loop was already three-quarters built and open at the last step. Skills are
described as "what previous runs distilled"; the investigation prompt already
tells the agent to grep `/data/results/` for earlier work on the same alert; the
skills API has had PUT and DELETE for weeks. Nothing ever produced a skill, and
production is still running with zero of them, so every investigation starts
from the same blank slate the last one did.

Two things it deliberately is not.

**Not an automatic write.** An investigator that edits its own future
instructions is one whose context nobody reviewed. One wrong conclusion —
already a normal outcome, since the reports say `confidence: 0.5` — would then
teach itself forward into every later investigation of that alert, and the
`inputs` record would faithfully show the memory digest changing with no human
in the history. The draft is the whole mechanism; approval is the gate.

**Not a second model call.** The run record already holds what was asked, which
tools ran in what order, and what was concluded. Paying a model to restate that
would cost money to introduce invention into the one artefact that should be
verbatim.

## Consequences

- Dead ends are not in the draft, and the draft says so. The record keeps
  `tool_use` events but not their results, so a listed step may have been a
  wasted one; the template asks the operator to delete those. Recording failures
  properly would mean capturing tool results, which is a separate decision about
  what the ledger is allowed to hold.
- The conclusion is taken from the **first** turn, not the last: the first turn
  is the investigation the family loop returns, later turns are an operator
  exploring. Taking the last one put "which model are you?" into a runbook, in
  the first draft this produced.
- Skills change the prompt prefix, so each saved runbook invalidates the cached
  prefix once. That is the trade the
  [prefix note](2026-08-14-the-prompt-prefix-is-not-ours-to-shrink.md) already
  priced: batch edits rather than saving one between every alert.
