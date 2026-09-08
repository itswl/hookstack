---
title: What the PRD asks for and will not get — four scope calls, with the evidence for each
status: proposed
date: 2026-09-08
scope: hookprobe
---

## Decision

Four items from the PRD are **not** being built, decided by the operator on
2026-09-08 after each was checked against the code and, where it mattered,
against production. They are written here so the next person to read the PRD
finds the reason instead of re-deriving the feeling.

## Why

A PRD is a list of things somebody wants; a codebase is a list of things
somebody will have to keep true. The four below were each checked against what
exists and, where a number could settle it, against production — and each turned
out to be either a second view of something already answered or a mechanism that
would damage the measurement it appeared to serve. Writing that down is cheaper
than deciding it twice, and a "no" with evidence behind it is worth more later
than a silent omission.

## Session pause

Not built. `stop` exists and ends a turn. For an alert investigator "pause" has
almost no meaning — a run is one turn and it is over in seconds. For the work
runner it has a little, and nobody has wanted it. Adding it would put a state in
the machine that nothing is ever observed in, which is the same reason
`received` and `triaged` are not states here either.

## A work detail page

Not built, and now with a measurement rather than an argument: **of 244 work
items on production, zero have more than one session.** A work item on one node
is one run, so a work detail page would show what the session page already
shows, character for character. Work that genuinely spans runs — a plan handed
to a work runner — spans NODES, and no single node's page can assemble it. That
belongs to the cross-node overview, which is deferred with its own reasons.

## Batch operations

Not built. The only population with any size is the 161 finished runs nobody has
ruled on, and a "rule all" button would be asking a person to pretend they made
161 judgements. That column is the only measurement of whether an investigation
was worth its bill; filling it with a gesture would destroy the one number the
cost argument rests on. The honest mechanism already exists and works: the
`run-rulings` patrol infers rulings from the evidence, labels them as inferences
(`ruled_by` starts with `patrol:`), and has produced 39 of them.

## An artifacts page

Not built. Artifacts are three things — a report, a distilled runbook, a
proposed procedure — and each already has the page that owns it: the session,
the skills browser, the actions list. A fourth view over the same three would be
surface with no question behind it, and the work item already lists what it
produced.

## Consequences

**The PRD's nine-item navigation will not appear**, and the reasons are now on
the record: five of its items are the board answering the same questions, one is
a page over data that does not exist yet (teams), one is this artifacts page,
and one is the cross-node overview.

**If any of these is wanted later, the evidence above is what to argue with** —
particularly the zero-of-244, which is a fact about how work is shaped on this
deployment and could change the day a node hands work to itself.
