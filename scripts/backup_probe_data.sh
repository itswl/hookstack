#!/usr/bin/env bash
# Daily tarball of the family's state, keep the last 7.
#
# probe-data holds the investigator's accumulated runbooks, memory, case files
# and audit log; shadow-data (when the shadow run exists) holds the ledgers the
# whole comparison exercise is for. Everything else (images, config) is a
# `git pull` away. Both are plain bind mounts precisely so this can be one tar
# with no docker involvement — a backup that needs the thing it protects to be
# healthy is not a backup.
#
# Install (host crontab):
#   15 4 * * * /srv/hookstack/scripts/backup_probe_data.sh
#
# Off-host copy (a backup on the same disk dies with it): set BACKUP_OFFHOST_DEST
# to an rsync target and each fresh tarball is mirrored there.
#
# Restore: scripts/restore_probe_data.sh <tarball> — it extracts, verifies every
# db snapshot opens, and prints the exact copy-back procedure.
set -euo pipefail

ROOT="${HOOKSTACK_ROOT:-/srv/hookstack}"
TARGET="${BACKUP_DIR:-/opt/backups/hookstack}"
KEEP="${KEEP:-7}"

members=()
for dir in probe-data shadow-data; do
  [ -d "$ROOT/$dir" ] && members+=("$dir")
done
# .env is the one part of the deployment a `git pull` cannot rebuild — every
# secret and every model alias. It rides in the tar so the backup can actually
# reconstitute the stack, not just its ledgers. It carries secrets: keep $TARGET
# host-local unless BACKUP_OFFHOST_DEST is trusted and encrypted at rest.
[ -f "$ROOT/.env" ] && members+=(".env")
[ ${#members[@]} -gt 0 ] || { echo "nothing to back up under $ROOT" >&2; exit 1; }
mkdir -p "$TARGET"

stamp="$(date +%F)"
tmp="$TARGET/.probe-data-$stamp.tgz.partial"

# The ledgers are WAL-mode SQLite, and a plain tar of a live one grabs db,
# -shm and -wal at three different instants — a copy that can refuse to open,
# which is the one failure a backup exists to not have. So before the tar,
# write a CONSISTENT `<name>.db.snapshot` beside each db via the SQLite backup
# API (safe against a concurrent writer; it takes read locks page by page).
# The snapshots ride inside the tar because they live inside the member dirs.
#
# Best-effort ON TOP of the raw copy, never instead: a locked or absent db, or
# a host without python3, degrades to today's behaviour (raw files, torn-copy
# risk) rather than to no backup — a backup that needs the thing it protects
# to be healthy is not a backup, and that doctrine covers its databases too.
snapshots=()
while IFS= read -r db; do
  snap="$db.snapshot"
  if python3 - "$db" "$snap" <<'PYEOF'
import sqlite3, sys
src = sqlite3.connect(sys.argv[1])
dst = sqlite3.connect(sys.argv[2])
with dst:
    src.backup(dst)
dst.close(); src.close()
PYEOF
  then snapshots+=("$snap")
  else echo "warning: could not snapshot $db — the raw copy is in the tar, torn-copy risk stands" >&2; rm -f "$snap"
  fi
done < <(find "${members[@]/#/$ROOT/}" -maxdepth 3 -name '*.db' 2>/dev/null)
# Clean up after ourselves however this run ends. The partial is dot-prefixed
# with a .partial suffix, which makes it invisible to the retention glob below
# and to the closing count — so a run that died mid-tar left one behind for
# good, and they accumulated where nobody looks until the backup volume was the
# thing that needed rescuing.
trap 'rm -f "$tmp" "${snapshots[@]:-}"' EXIT
# Leftovers from before this trap existed, and from the one death a trap cannot
# catch (SIGKILL, a hard reboot). Only once they are a day old: a concurrent run
# writing right now must not have its file pulled out from under it.
find "$TARGET" -maxdepth 1 -name '.probe-data-*.tgz.partial' -mtime +0 -delete 2>/dev/null || true

# One tar, atomically renamed: a backup killed mid-write must not look like a
# backup. --warning=no-file-changed because runs write while we read; a file
# torn mid-append (audit JSONL) is still a better copy than no copy — and the
# `|| [ $? -eq 1 ]` is what turns that warning back into success.
#
# The flag is GNU tar's, though, and macOS ships bsdtar, which refuses an option
# it does not know rather than ignoring it ("Option --warning=no-file-changed is
# not supported") — so on a laptop this died before writing a byte and the `mv`
# then failed on a file that was never created. Ask tar what it is first.
# bsdtar needs no equivalent: it is already quiet about files that change under
# it, and its non-zero exit is covered by the same tolerance.
quiet=""
tar --version 2>/dev/null | grep -qi 'gnu tar' && quiet="--warning=no-file-changed"
tar -czf "$tmp" ${quiet:+"$quiet"} -C "$ROOT" "${members[@]}" || [ $? -eq 1 ]
# Verify the archive lists end to end BEFORE it replaces yesterday's. A
# truncated or corrupt tarball that silently overwrote the last good one is the
# second failure a backup exists to not have; the raw-copy tolerance above buys
# nothing if the container itself is unreadable. exit here fires the trap, so
# the partial and the snapshots are cleaned and the previous backup stands.
tar -tzf "$tmp" >/dev/null 2>&1 || { echo "archive failed verification, keeping the previous backup" >&2; exit 1; }
mv "$tmp" "$TARGET/probe-data-$stamp.tgz"

# Keep the newest KEEP, delete the rest — by name, which is by date.
#
# Newest-first, then drop the first KEEP lines. The obvious spelling of this is
# `sort | head -n -"$KEEP"`, and it worked on the deployment host and nowhere
# else: a negative line count is a GNU coreutils extension, and BSD head — which
# is macOS head, which is where this repository is developed — answers "illegal
# line count" and exits non-zero. Under `set -e` that is a backup script that
# reports failure after having taken a perfectly good backup. `tail -n +N` is
# POSIX and means the same thing read from the other end.
ls -1 "$TARGET"/probe-data-*.tgz 2>/dev/null | sort -r | tail -n "+$((KEEP + 1))" | while read -r old; do
  rm -f "$old"
done

echo "backed up $(du -h "$TARGET/probe-data-$stamp.tgz" | cut -f1) -> $TARGET/probe-data-$stamp.tgz ($(ls -1 "$TARGET"/probe-data-*.tgz | wc -l | tr -d ' ') kept)"

# Optional off-host mirror. A backup that shares a disk with the thing it
# protects dies with it — this instance is kept alive by one health-check bot
# and nothing else. Left unset = no copy; a failed copy warns but never fails
# the local backup (which already succeeded). The tarball carries .env, so the
# destination must be trusted and encrypted at rest.
if [ -n "${BACKUP_OFFHOST_DEST:-}" ]; then
  if rsync -a "$TARGET/probe-data-$stamp.tgz" "$BACKUP_OFFHOST_DEST"/ 2>/dev/null; then
    echo "mirrored -> $BACKUP_OFFHOST_DEST"
  else
    echo "warning: off-host copy to $BACKUP_OFFHOST_DEST failed — the local backup stands" >&2
  fi
fi
