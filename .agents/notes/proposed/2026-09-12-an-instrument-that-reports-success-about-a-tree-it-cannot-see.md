---
title: Four instruments in one day reported success about a tree, a moment or a machine they were not looking at
status: proposed
date: 2026-09-12
scope: stack
---

## Decision

Record the class, and fix only the part that can be fixed without building a
capability: a check that cannot look where the reader assumes should NAME where
it did look. `assert_no_estate_identifiers.py` now prints
`… in the working tree at <repo>@<sha> …` rather than a bare count.

The capability it lacks — scanning an arbitrary ref — is deliberately NOT added
here. It is a security check two sessions rely on, and the honest sequence is
to stop it being misread first, then decide whether it should grow.

## Why

**Four failures in two days, and every one had the same shape.** Not "the tool
was wrong" — the tool answered a question correctly, about a subject that was
not the one being asked about, and reported success.

1. **`echo raw=[$VAR]`** (hookstack-34, probing a container). Renders unset and
   empty identically as `[]`, and the entire question was whether the variable
   was unset or empty. Re-measured with `printenv` and its exit code.
2. **`test_output_guard` reading `iterdir()`** (mine). Read the first entry the
   filesystem happened to yield and parsed it as JSON. macOS yielded the day
   file, Linux yielded `.chain`. Green on every local gate, red on CI.
3. **`git grep -E "\\b…"`** (mine, scanning release tags for a leaked project
   name). `git grep -E` does not support `\\b`, so it reported the tag clean
   while the name sat in its tree. I was one message from telling the operator
   the exposure was gone.
4. **`assert_no_estate_identifiers.py` in a detached worktree** (mine, same
   hunt). Run inside four worktrees checked out at four release tags, it
   printed a clean verdict four times — because `ROOT` is the script's own
   location and `tracked_files()` runs `git ls-files` with `cwd=ROOT`. It had
   scanned the same working tree four times. The real answer, taken by running
   the guard's own rules over `git ls-tree` blobs, was that two of the four tags
   carried an identifier.

**The through-line is hookstack-34's sentence and it is better than mine:**
every one was an instrument reporting success about a tree, a moment or a
machine it was not actually looking at. Not a wrong answer — an answer to a
different question, in the shape of the expected one.

**Which makes the fix a reporting fix before it is a capability fix.** (3) and
(4) were both *silent*: no error, no warning, a success line. Had either said
what it had examined, the mismatch would have been visible in the same glance
that read the verdict.

## Consequences

* **`assert_no_estate_identifiers.py` can still only see the working tree**,
  and now says so on every run. Anyone who needs a tag or a historical tree
  scanned must do what this hunt ended up doing: load `load_patterns()` and run
  them over `git ls-tree -r <ref>` blobs, skipping undecodable ones.
* **A `--ref` flag is the obvious next step and is not taken here.** It changes
  what a security check reads, at the end of a long session, in a repository
  where the same check is quoted as evidence by more than one session — the
  same call that kept a copy-based runner out of the mutation checker
  ([[the-mutation-window-is-a-shared-tree-hazard]]).
* **The generalisation is uncomfortable and worth stating plainly.** This
  repository's whole method is "measure, do not assume", and four times in two
  days the measurement was the thing that lied. Measuring is not sufficient;
  what the instrument is pointed at has to be measured too, which is
  [[measure-the-thing-the-design-depends-on]] aimed one level lower than it was
  written for.
* Related: the tags themselves were the finding, not the guard — see the
  privacy remediation of 2026-09-12, where two published releases were deleted
  after the rules were extended and synced to both secret stores.
