---
title: Infra hardening the review found and this pass deliberately left
status: proposed
date: 2026-09-02
scope: stack
---

## Decision

Second pass, 2026-09-02: several items below shipped; the rest still need a
decision, a destination, or a riskier change.

DONE this day (see the implemented notes):
- **cap_drop:[ALL] + no-new-privileges on the pipe and the brain**, and
  **dependabot `docker` ecosystem** → [[the-pipe-and-the-brain-run-non-root]].
- **Backup verification, `.env` coverage, off-host hook, restore script** →
  [[backups-verify-themselves-and-can-leave-the-host]]. The remaining decision
  is only WHERE off-host goes — the mechanism is one env var now.

Still deferred:

- **Non-root uid for the pipe and the brain, and the CI `requirements.lock`
  pin.** Both edit `.github/workflows/*` (the non-root uid needs the CI
  docker-smoke's bind mount chowned) and the push token lacks the `workflow`
  scope. One branch, behind `gh auth refresh -h github.com -s workflow`.

- **deploy.sh rollback + a version stamp.** Mutable local tags
  (`hookrelay:shadow`, `hookprobe:latest`) mean the previous image is untagged
  and pruned, so rollback is a rebuild of an older commit; `/healthz` and the
  image labels carry no git SHA. Propose: tag each build with the commit, keep
  the last N, stamp the SHA into a label the board reads.
- **Health read-back checks absence, not presence.** deploy.sh breaks when
  nothing is `unhealthy`/`starting`; a crash-looping `lark-bridge` (no
  HEALTHCHECK) passes as "deployed and healthy". Give the bridge a healthcheck,
  or assert each expected container is `healthy`.
- **lark-bridge non-root.** The other three services dropped to uid 10001; the
  bridge stays root (node:22-alpine, a different user model, and it is the live
  delivery path — a uid mistake there drops the operator's cards). Do it on its
  own, with a config-volume chown.
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

- The biggest standing risk is now narrow: backups verify and a restore is
  written, but nothing leaves the host until `BACKUP_OFFHOST_DEST` is set to a
  real (trusted, encrypted) destination. Setting it is the single most valuable
  next action; the code is already there.
- After that, deploy rollback: a bad ship still means rebuilding an old commit,
  because the previous image was pruned. A commit-stamped, retained tag is the
  fix.
- None of these blocks the fixes that shipped; they are additive.
