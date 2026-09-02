---
title: Infra hardening the review found and this pass deliberately left
status: proposed
date: 2026-09-02
scope: stack
---

## Decision

Not built in the 2026-09-02 review-fix pass, each for a reason worth recording
rather than rediscovering. The pass took the security and correctness fixes that
were small and low-regression; these need a decision, a destination, or a
riskier change.

- **Off-host backups + a restore procedure.** `backup_probe_data.sh` keeps 7
  daily tarballs on the SAME disk as the data (it does snapshot the WAL-mode
  SQLite ledgers correctly). Host loss = ledgers and investigator memory gone,
  and there is no restore doc anywhere. Needs a destination the operator picks
  (an object store / another host) and a documented, tested restore. This is the
  highest-value item left.
- **Non-root images for the pipe, the brain and the bridge.** Only hookprobe
  sets `USER`. Switching the others is a one-line Dockerfile change PLUS a chown
  of the existing `shadow-data/*` bind mounts (currently root-owned), so it must
  be done with the ownership migration or the containers cannot write their
  ledgers. Deferred to avoid a mid-review deploy that can't start.
- **deploy.sh rollback + a version stamp.** Mutable local tags
  (`hookrelay:shadow`, `hookprobe:latest`) mean the previous image is untagged
  and pruned, so rollback is a rebuild of an older commit; `/healthz` and the
  image labels carry no git SHA. Propose: tag each build with the commit, keep
  the last N, stamp the SHA into a label the board reads.
- **Health read-back checks absence, not presence.** deploy.sh breaks when
  nothing is `unhealthy`/`starting`; a crash-looping `lark-bridge` (no
  HEALTHCHECK) passes as "deployed and healthy". Give the bridge a healthcheck,
  or assert each expected container is `healthy`.
- **CI installs deps without `-c requirements.lock`** while the images install
  through it, so CI can be green on versions the image never ships. Add `-c` in
  the three workflows. `dependabot` has no `docker` ecosystem, so base images are
  never nagged.
- **lark-bridge.** Inbound POST is unauthenticated on the shared platform
  network, and `entrypoint.sh` keeps an existing config so rotating
  `LARK_APP_SECRET` silently needs the named volume deleted (the compose comment
  claims otherwise). Both are bridge changes, out of scope for this pass.
- **Guard breadth.** The bash guard now catches line-continuation, `oc`,
  `kubecolor`, `helmfile`; still by-design open on cloud CLIs, `curl -X DELETE`,
  and process kills — the read-only credentials remain the real boundary
  (guard.py docstring).

## Why

Each of these is either a decision the operator owns (where do backups go), a
change that needs a data migration to be safe (non-root + chown), or a
CI/deploy-shape change better landed on its own than bundled into a security
fix. Writing them down keeps them from being silently forgotten now that the
loud items are fixed.

## Consequences

- The biggest standing risk after this pass is the backup: green everything
  else, and a lost host still loses the ledgers. That one should be next.
- None of these blocks the fixes that shipped; they are additive.
