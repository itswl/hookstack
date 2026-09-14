---
title: Ten instruments in three days: eight reported success about a set they never examined, two answered truthfully and were misread
status: proposed
date: 2026-09-12
scope: stack
---

## Decision

*Written 2026-09-12 after four of these; extended 2026-09-14 to ten, and to
the second countermeasure, which the first version did not have.*

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

5. **`gh run view --log`** (hookstack-34, sampling CI logs for identifiers).
   Returned **0 hits over 0 lines** — the command had produced no log at all.
   Caught only because the line count was printed beside the hit count.
6. **`git-filter-repo --replace-text`** (mine, scrubbing the internal tool
   name). Printed *"New history written… Completely finished"* and changed
   nothing: the guard's rule matches case-insensitively, filter-repo's literal
   form does not, and the occurrence was mixed-case against a lowercase
   literal derived from the rule. `HEAD` not moving was the only tell.
7. **A `cat-file --batch` scanner** (mine). Skipped non-blob objects without
   consuming their payload, desyncing the pipe on the first tree. Three runs
   hung across eight minutes while "no output yet" read as "still working".
   `ps` showing 0.1s of CPU is what exposed it.
8. **`until [ -s file ]`** (mine, waiting for a gate). Satisfied instantly by a
   `git log` line written earlier in the same pipeline, so the gate was declared
   finished while still running.
9. **`git reset --hard origin/main`** (hookstack-34, after the repo was
   recreated). `git fetch` had FAILED — the fresh repo did not know the key —
   and the reset then succeeded against a three-hour-old remote-tracking ref,
   exit 0. Caught by printing the resulting SHA beside the expected one.
10. **`gh api -X PATCH … validity_checks=enabled`** (the operator's own
    command). Returned 200 with the field still `disabled`. Silent no-op, and
    only a fresh `GET` showed it.

**The through-line is hookstack-34's sentence and it is better than mine:**
every one was an instrument reporting success about a tree, a moment or a
machine it was not actually looking at. Not a wrong answer — an answer to a
different question, in the shape of the expected one.

**Which makes the fix a reporting fix before it is a capability fix.** (3) and
(4) were both *silent*: no error, no warning, a success line. Had either said
what it had examined, the mismatch would have been visible in the same glance
that read the verdict.

**But there is a second failure mode with a different countermeasure, and a
note recording only the first teaches half the lesson.** hookstack-34's
distinction: printing the denominator catches an instrument that examined
nothing. It does nothing when the instrument ran fine, returned a real result,
and the reader attributes it to the wrong cause. Two of these, both on
2026-09-14 while verifying the Python 3.14 image:

* The spawned gate answered `{}` under 3.14 and looked like a broken
  containment boundary on a new interpreter. **The 3.12 control answered `{}`
  too** — the payload was missing `hook_event_name`, and neither image was
  broken.
* The suite in-image reported `2 failed, 619 passed` and the two looked like
  3.14 regressions. **Identical under 3.12** — the ad-hoc harness was missing
  `scripts/` and `.github/`.

Both results were true. Both would have been reported as findings about 3.14.
What made them uninterpretable as findings was running the known-good control
BEFORE attributing a cause — and neither would have been caught by printing
what was examined, because what was examined was correct.

So the pair, and they are not substitutes:

| countermeasure | catches |
| --- | --- |
| print what was examined beside the verdict | the instrument that examined nothing and said success |
| run the known-good control before attributing a cause | the instrument that ran, answered truthfully, and was misread |

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
