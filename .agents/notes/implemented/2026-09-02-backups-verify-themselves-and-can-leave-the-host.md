---
title: Backups verify themselves, carry .env, and can leave the host
status: implemented
date: 2026-09-02
scope: stack
---

## Decision

`scripts/backup_probe_data.sh` gains three things, and `scripts/restore_probe_data.sh`
is new:

- **Verification before replace.** The fresh tarball is `tar -tzf`'d before the
  atomic `mv`; a corrupt or truncated archive aborts (firing the cleanup trap)
  and leaves yesterday's good backup in place, instead of silently overwriting
  it.
- **`.env` rides along.** It is the one part of the deployment a `git pull`
  cannot rebuild (every secret, every model alias). Without it a "restore" could
  reconstruct the ledgers and still not boot the stack.
- **Optional off-host mirror.** `BACKUP_OFFHOST_DEST`, unset by default, rsyncs
  each tarball off the box; a failed copy warns but never fails the local
  backup.
- **A restore script.** It extracts, runs `PRAGMA integrity_check` on every
  `*.db.snapshot`, and prints the exact copy-back procedure. It does NOT touch
  the live tree or containers — the destructive step stays a human's.

## Why

The review's highest-value gap: backups were 7 tarballs on the SAME disk as the
data, with no integrity check and no restore procedure anywhere — and this host
is kept alive by a single health-check bot. A backup you have never restored is
a hope. The snapshot mechanism (consistent `.db.snapshot` via the SQLite backup
API) was already correct; what was missing was proof the archive is readable,
the one file that makes it bootable, a way off the host, and a written,
verifiable restore.

The restore script encodes the one fact that is easy to get wrong under
pressure: promote each `.db.snapshot` over the live `.db` and drop the stale
`-wal`/`-shm`, because the raw `.db` in the tar was read live and may be torn.

## Consequences

- Off-host is opt-in and the operator must choose the destination — the tarball
  carries `.env`, so it must be somewhere trusted and encrypted at rest. This is
  the remaining decision; the mechanism is now one env var.
- The tar is slightly larger (one small file) and the daily run does one extra
  `tar -tzf` pass — cheap insurance.
- Restore is staged-and-printed, not one-command. A fully automated `--apply`
  was left out deliberately: it clobbers live ledgers, and an untested
  auto-restore is its own foot-gun. If it is wanted later, build it on top of
  the staging the script already does.
