---
title: The pipe and the brain drop every capability (non-root uid deferred)
status: implemented
date: 2026-09-02
scope: stack
---

## Decision

`deploy/docker-compose.shadow.yml` adds `cap_drop: [ALL]` and
`security_opt: [no-new-privileges:true]` to hookrelay, hookjudge and hookjudge-b.
Before this, only hookprobe had any container hardening; the pipe and the brain
ran with the full default capability set.

Dependabot gained a `docker` ecosystem entry per Dockerfile (base images were
the one thing nothing nagged).

**Deferred in the same pass: running as a non-root uid.** The Dockerfile `USER`
change was written and then backed out, because it needs a change I cannot make
here — see Why.

## Why

`cap_drop: [ALL]` is the high-value, low-risk half: an HTTP server that reads
config and writes a SQLite ledger needs no Linux capability, and dropping them
(plus `no-new-privileges`) removes `CAP_DAC_OVERRIDE`, `CAP_SETUID`, `CAP_CHOWN`
and the rest even though the process is still uid 0. It applies at runtime to the
shadow deployment and touches nothing CI builds.

The non-root uid (10001, to match hookprobe) is strictly better but coupled to
two things this session could not land:

- **CI's docker-smoke** (`.github/workflows/ci.yml`) runs the image with
  `-v /tmp/relaydata:/data`, a bind mount the runner creates as its own uid.
  A non-root container cannot open the DB there — verified: the job failed with
  `sqlite3.OperationalError: unable to open database file`. Fixing it means
  chowning that dir (or using a named volume) in the workflow.
- **The push credential** for this repo (gh's `itswl`) lacks the `workflow`
  scope, so it cannot push any edit to `.github/workflows/*`. The
  `requirements.lock` CI pin is blocked on the same thing.

So the honest split is: ship the capability drop now, and land non-root together
with the CI fixes once a `workflow`-scoped token is available
(`gh auth refresh -h github.com -s workflow`). The Dockerfile change is a
`USER app` after a `useradd 10001 && chown /data`; the shadow bind mounts then
need `chown -R 10001:10001`.

## Consequences

- The three shadow services now run with no capabilities and no privilege
  escalation. Verify after deploy that they still boot and write their ledgers
  (a root process minus `CAP_DAC_OVERRIDE` still writes files it OWNS, and
  shadow-data stays root-owned, so this is expected to be transparent).
- `/data` ownership is unchanged (still root) — do NOT chown it until the
  non-root uid actually ships, or the current root containers lose write.
- The follow-up is one branch: ci.yml bind-mount chown + the four Dockerfile
  `USER` lines + `requirements.lock` pins, behind a workflow-scoped push.
