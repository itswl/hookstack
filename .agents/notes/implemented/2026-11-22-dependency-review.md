---
title: Every PR carries a dependency-review check
status: implemented
date: 2026-11-22
scope: stack
---

## Decision

`.github/workflows/dependency-review.yml` runs `actions/dependency-review-action`
on every pull request, with no path filters: all four components' dependency
graphs are diffed in one check.

## Why

The component gates verify that a service still works with its pinned set;
none of them reads the *change* — which vulnerabilities, licences or flagged
packages a PR introduces across the graphs it touches. A bump that merges
several components' transitive dependencies is exactly what no single
component's lockfile diff shows clearly. The action is free on public repos
and reads the dependency graph the PR already carries.

## Consequences

Easy: one check per PR covering everything; findings appear on the PR, not
in a weekly sweep. To watch: the action's default threshold fails on
low-severity findings — if that proves noisy on routine bumps, tune
`fail-on-severity` rather than removing the workflow.
