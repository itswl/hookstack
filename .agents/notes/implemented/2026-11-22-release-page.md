---
title: A tag now also cuts the GitHub Release, after promote
status: implemented
date: 2026-11-22
scope: stack
---

## Decision

`release.yml` gains a `release` job, `needs: [promote]`: once `:latest` has
moved for all four images, it creates the GitHub Release for the tag with
generated notes (idempotent — an existing release is left untouched).

## Why

Fifteen tags existed and the Releases page was empty: the workflow published
images and moved `:latest`, but no Release object, so the changelog a tag
promises had nowhere to live. The job runs after `promote` on purpose — a
page saying "shipped" while `:latest` still points at the previous version
would be the split stack again, this time in prose.

## Consequences

Easy: every future tag gets a page with the commits since the previous one.
The fifteen existing tags were backfilled by hand (`gh release create
--generate-notes`, oldest first so the ranges chain correctly); later tags
need nothing. To watch: `--verify-tag` means a `workflow_dispatch` run for a
version whose tag does not exist still fails here, as it should.
