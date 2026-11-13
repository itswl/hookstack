---
title: The services run Python 3.15, moved as one change with the locks and CI
status: implemented
date: 2026-11-13
scope: stack
---

## Decision

The pipe, the judge and the investigator move from `python:3.14-slim` to
`python:3.15-slim`, together with the four things that have to move with
them:

- each service's CI pin and the release gate matrix;
- the stdlib helper containers in the compose files, so the project has one
  interpreter: the demo's sink and stub model, the work stack's sidecars, the
  production compose's;
- the locks, for four packages that ship no 3.15 wheel at the locked version
  (pydantic 2.13.4 → 2.14.3 for pydantic-core 2.46.4 → 2.50.4, httptools
  0.8.0 → 0.9.0, uvloop 0.22.1 → 0.23.0), and pip-api 0.0.34 → 0.0.35;
- the local venvs the gate runs, rebuilt on 3.15.

Dependabot's three base-image PRs (#56–#58) are closed in favour of this.

## Why

Dependabot proposed the bump as three one-line `FROM` changes, and each
failed two ways (2026-11-13):

- `assert_python_pins` refused an image on 3.15 tested by CI on 3.14. That is
  the check doing its job: the bump has to move the CI pins in the same change.
- The image build failed compiling pydantic-core. The locked version has no
  3.15 wheel, and the slim image has no compiler or Rust toolchain.

The 3.15 wheels exist one release up for pydantic-core, httptools and uvloop.
PyYAML has no 3.15 wheel in any release, but on `python:3.15-slim` its sdist
installs as pure Python (no C loader), measured. The services load YAML only
at startup, for config, so the slower loader costs nothing that matters.

The gate on 3.15 found one real incompatibility the image build never would
have, because the build installs no dev tools: `pip-audit` imports pip-api,
whose vendored pyparsing imported `sre_constants`, removed in 3.15. pip-api
0.0.35 (2026-09-08, its issue #294) fixes it.

## Consequences

- Measured before the PR, on 3.15.2:
  - the full gate green, every suite at its 3.14 count (hookrelay 365,
    hookjudge 196, hookprobe 953), and the pipe and judge images built;
  - the investigator's image built and the offline engine check unchanged: the
    turn completes, an allowed read runs, a protected write is refused and
    never runs, and an `@path` is not inlined.
- The published images stay on 3.14 until the next release; nothing deployed
  changes before then.
- PyYAML stays a source build until it ships a 3.15 wheel. A future PyYAML
  that needs a compiler to install at all would fail the image build, loudly.
- A developer's venv on 3.14 no longer matches what ships. The gate's own
  instructions (`python3 -m venv .venv`) pick up whatever `python3` is, so
  build venvs with `python3.15` explicitly.
