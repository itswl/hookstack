---
title: A real profile name was the compose default, and the guard that should have caught it was skipping
status: implemented
date: 2026-09-12
scope: stack
---

## Decision

**1. The work node's AWS profile is required now, not defaulted.** The line in
`deploy/docker-compose.work.yml` read `${PROBE_PLAN_AWS_PROFILE:-<a real VPN
profile name>}`; it now reads `${PROBE_PLAN_AWS_PROFILE:?set
PROBE_PLAN_AWS_PROFILE in .env}`, which is what the other twenty secret-shaped
settings in that file already did. There is no default available here: any value
that WORKS is the real profile name, so the only honest form is the one that
fails at compose parse instead of resolving at runtime.

**2. The name was scrubbed out of the whole history, not just off the tip.** It
entered in `deploy(work): the write node's first credential is a read-only one`
(2026-09-05) and had been public for seven days. `git filter-repo
--replace-text` rewrote that commit and its 163 descendants — 164 of 481 — to an
obviously fictional placeholder, and the tip then takes the `:?` form above.
SHAs from that commit forward are new; a clone taken before today cannot be
merged, it has to be re-cloned.

**3. The pattern list stops being optional.** `scripts/assert_no_estate_identifiers.py`
exits 0 with `SKIP no pattern list` when `.estate-identifiers` is absent, and on
the operator's laptop it was absent — only the `.example` was there, so the guard
had been inert locally since it was ported and this is the paste it did not
catch. A list drafted from the same week's audit now exists there. It stays
git-ignored, which is what makes it usable: the file is the one place the real
names are allowed to be written down.

## Why

The guard's own docstring names this failure and its remedy: *"without
ESTATE_GUARD_REQUIRED=1 a missing list would make this exit 0 and the protection
would evaporate silently, which is the failure mode the guard exists to
prevent."* Locally that is exactly what happened. The mechanism was installed,
the words were not, and `SKIP` reads as a pass in a gate where everything else
reads as a pass too.

CI did not cover for it either, and that part is checkable rather than
theoretical: `ci-stack.yml` writes the list from `ESTATE_IDENTIFIERS` and runs
the guard on every push to main, and the runs on the three commits that followed
this one — all of which carried the name in a tracked file — concluded
`success`. So the secret is missing this pattern as well, and a scrub on this
machine alone would leave CI green over the next paste of the same name.

The default is the second-order half. A knob whose default has to be a real name
cannot be defaulted at all, and `:?` moves the failure from the container's
first AWS call — silent, inside a run that has already been handed work — to
`docker compose` refusing to parse, which happens before anything is stopped.
Same trade the event secret two lines above it already made.

## Consequences

- `PROBE_PLAN_AWS_PROFILE` has to be in the deploy host's `.env` before the next
  `up` of the work compose or compose refuses to start. Nothing is torn down by
  that refusal: it fails at parse, ahead of `up`.
- 164 commit SHAs changed. Any other clone must be re-cloned; a merge from an old
  copy resurrects the name.
- The name is gone from the reachable history, not from GitHub's object store.
  A commit reachable only by SHA stays fetchable by anyone holding the SHA, and
  anything that cached the name before today — a clone, a crawler, an index — is
  outside the reach of this scrub and stays outside it.
- CI needs the same pattern, or this failure returns in the one place the local
  guard cannot see: `gh secret set ESTATE_IDENTIFIERS --repo itswl/hookstack <
  .estate-identifiers`.
