---
title: The README names the SDLC stage this family serves
status: implemented
date: 2026-08-31
scope: stack
---

## Decision

One section in the root README maps the family onto the vocabulary of
Anthropic's two AI-native-SDLC posts (the playbook, and how they secure their
own): this stack is the **Maintain** stage — deterministic answers before paid
ones, propose-not-act on anything irreversible, agents as monitored actors
rather than trusted authors. Links to both posts; claims limited to what a
check in this repository already enforces.

## Why

People who adopt that playbook will look for tooling in its words, and until
now not one of those words appeared in this repository — the overlap existed
only as architecture. The mapping costs one section and holds without new
claims: the route order (most events never reach `ai`), the eval gate between
build and up, the probe's four-layer read-only posture, service-written
runbooks, the red-team smoke on the memory path. Where the playbook and this
stack differ — it describes mature workflows drifting toward auto-accept,
this stack keeps hard write-gates on anything irreversible — the README keeps
this stack's side and does not pretend otherwise.

## Consequences

- One home only, the root README. assert_docs' own doctrine applies: two
  copies of one claim, and the one further from the code rots first. OVERVIEW
  and the component READMEs stay as they were.
- The section links external posts; check-docs only verifies relative links,
  so a moved post fails soft. Accepted.
- Every sentence in that section is held by an existing check or test.
  Anything added later has to clear the same bar — a sentence there that no
  check holds is the "~1400 lines with tests" defect again.
