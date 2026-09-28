---
title: The stack job caches its images and skips what it need not run — CI minutes, measured and cut
status: implemented
date: 2026-09-28
scope: stack
---

## Decision

Four changes to what the hosted runners do for this repository, each aimed at a
measured cost and none at what the checks cover:

1. **The stack job bakes the four images with a layer cache** before the smoke
   runs, and the smoke is told so (`STACK_PREBUILT=1` skips `--build`; by hand
   it still builds). The compose file now names the images it builds so a
   pre-built one is the one compose looks for. If the bake fails, the smoke
   builds what is missing — a cache problem costs minutes, never the answer.
2. **A push to main that is a pull request's merge commit runs nothing in
   `ci-stack`.** That pull request's own run tested the merge of its head with
   main. The residual — main moved between the PR's last run and its merge — is
   what a stale-PR banner shows a person merging, and the local gate is read
   before every merge here anyway.
3. **A push that changes only notes, docs and pictures does not boot the
   family.** Decided inside the docs job from the diff, erring to "run": a diff
   that cannot be computed counts as code. Nothing the stack boots reads
   markdown; the docs job, which does, still runs.
4. **The component workflows ignore their own `docs/` and markdown.** Those
   files are the docs job's business.

Not done: the release's arm64 image still builds under QEMU. A native arm
runner would cut that leg most, but whether the label is available to a
private repository on this plan is not something a workflow can find out
without a run, and releases are four a month.

## Why

On 2026-09-28 the account ran out of Actions minutes and every check failed in
three seconds. Read from the billing API and the runs API for the month:

| | |
| --- | --- |
| account, Linux-equivalent minutes (macOS ×10, Windows ×2) | about 7,700 vs 2,000 included |
| this repository, current form, two weeks | about 450 job-minutes |
| of which the stack job | 110 |
| of which the stack job spent building images | about 110 of every 140 seconds |
| pushes to main, two weeks | 55, of which 9 were PR merges already tested as PRs |
| pushes to main that were notes or docs only | 5 |

The other two thirds of the account's usage are in two other repositories and
are addressed there; this repository alone could not reach the included
minutes, and the note says so because the number is on the same page as the
fix.

## Consequences

Roughly half of this repository's minutes at the current pace. A first run
after a Dockerfile change pays the full build once and refills the cache; the
GitHub Actions cache holds ten gigabytes per repository and evicts the oldest,
which the four images fit inside many times over.

What is no longer checked: the merged tree on main, when the PR that produced
it was merged after main moved. What was never checked and still is not: a
workflow file's own validity, which only a run proves — and none ran when this
landed, because the minutes were gone. The first push after billing is
restored is that run; read it back.

## Rejected

- **A separate job to decide whether to run.** Billed at a minute minimum,
  which on this repository's mix of direct pushes and PR merges costs more than
  it saves. The decision rides inside a job that runs anyway.
- **Running CI only on pull requests.** Peers push straight to main; a green
  gate lied for five pushes once, and the answer to that was reading CI back,
  not reading it less.
- **Dropping the arm64 image from releases.** The operator's machine is arm64;
  the quickstart pulls that image.
