#!/usr/bin/env bash
# Restore — or rehearse restoring — a hookstack backup taken by
# backup_probe_data.sh. This existed nowhere, which meant the backups were
# untested: a tarball you have never restored is a hope, not a backup.
#
#   scripts/restore_probe_data.sh <tarball>
#
# It extracts the archive to a staging dir, VERIFIES every SQLite snapshot in it
# actually opens (the whole reason the backup writes <name>.db.snapshot beside
# each live db), and prints the exact copy-back procedure. It deliberately does
# NOT touch $HOOKSTACK_ROOT or any container: a restore overwrites live ledgers
# and the operator must be the one to stop the stack and run the copy, with the
# staged tree in front of them. The dangerous step is a human's to take.
#
# The key fact the printed procedure encodes: restore each db from its
# .db.snapshot (the consistent copy), NOT from the raw .db in the tar (which was
# read live and may be torn), and drop the stale -wal/-shm so SQLite rebuilds
# from the restored db.
set -euo pipefail

ARCHIVE="${1:-}"
[ -n "$ARCHIVE" ] && [ -f "$ARCHIVE" ] || { echo "usage: restore_probe_data.sh <tarball>" >&2; exit 2; }
ROOT="${HOOKSTACK_ROOT:-/srv/hookstack}"

stage="$(mktemp -d "${TMPDIR:-/tmp}/hookstack-restore.XXXXXX")"
echo "staging $ARCHIVE -> $stage"
tar -tzf "$ARCHIVE" >/dev/null 2>&1 || { echo "archive will not list — it is corrupt or truncated" >&2; exit 1; }
tar -xzf "$ARCHIVE" -C "$stage"

echo
echo "== contents =="
( cd "$stage" && find . -maxdepth 2 -mindepth 1 | sort | sed 's/^/  /' )

echo
echo "== snapshot verification (each must say OK) =="
fail=0
while IFS= read -r snap; do
  rel="${snap#"$stage"/}"
  if python3 - "$snap" >/dev/null 2>&1 <<'PYEOF'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
c.close()
PYEOF
  then echo "  OK   $rel"
  else echo "  FAIL $rel — this snapshot does not open; do not restore from it"; fail=1
  fi
done < <(find "$stage" -name '*.db.snapshot' 2>/dev/null)
[ -n "$(find "$stage" -name '*.db.snapshot' 2>/dev/null)" ] || echo "  (no db snapshots in this archive — ledgers may be raw copies only)"

echo
echo "== copy-back procedure (run by hand, with the stack stopped) =="
cat <<EOF
  # 1. Stop the projects that own the data:
  docker compose -p hookstack-shadow -f $ROOT/deploy/docker-compose.shadow.yml down
  docker compose -p hookprobe-prod  -f $ROOT/hookprobe/deploy/docker-compose.prod.yml down

  # 2. Move the current state aside (never delete it — this restore is reversible too):
  mv $ROOT/probe-data  $ROOT/probe-data.pre-restore.\$(date +%s)   2>/dev/null || true
  mv $ROOT/shadow-data $ROOT/shadow-data.pre-restore.\$(date +%s)  2>/dev/null || true

  # 3. Copy the staged tree into place:
  cp -a $stage/probe-data  $ROOT/  2>/dev/null || true
  cp -a $stage/shadow-data $ROOT/  2>/dev/null || true
  [ -f $stage/.env ] && cp -a $stage/.env $ROOT/.env   # secrets — review before trusting

  # 4. For every ledger, promote the CONSISTENT snapshot over the (possibly torn)
  #    live copy and drop the stale WAL so SQLite rebuilds from it:
  find $ROOT/probe-data $ROOT/shadow-data -name '*.db.snapshot' | while read -r s; do
    db="\${s%.snapshot}"; cp -a "\$s" "\$db"; rm -f "\$db-wal" "\$db-shm"
  done

  # 5. Ownership the non-root containers expect, then bring it back up:
  sudo chown -R 10001:10001 $ROOT/probe-data $ROOT/shadow-data
  cd $ROOT && ./scripts/deploy.sh
EOF
echo
echo "staged tree left at $stage for inspection; remove it when done."
