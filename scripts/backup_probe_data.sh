#!/usr/bin/env bash
# Daily tarball of the investigator's state, keep the last 7.
#
# probe-data is the only state in the family worth a backup: the runbooks the
# learning loop accumulated, the environment memory, case files, transcripts,
# the audit log. Everything else (images, config) is a `git pull` away. The
# volume is a plain bind mount precisely so this can be one tar with no docker
# involvement — a backup that needs the thing it protects to be healthy is not
# a backup.
#
# Install (host crontab):
#   15 4 * * * /opt/docker-compose/hookstack/scripts/backup_probe_data.sh
set -euo pipefail

SOURCE="${PROBE_DATA_DIR:-/opt/docker-compose/hookstack/probe-data}"
TARGET="${BACKUP_DIR:-/opt/backups/hookstack}"
KEEP="${KEEP:-7}"

[ -d "$SOURCE" ] || { echo "nothing to back up: $SOURCE missing" >&2; exit 1; }
mkdir -p "$TARGET"

stamp="$(date +%F)"
tmp="$TARGET/.probe-data-$stamp.tgz.partial"
# One tar, atomically renamed: a backup killed mid-write must not look like a
# backup. --warning=no-file-changed because runs write while we read; a file
# torn mid-append (audit JSONL) is still a better copy than no copy.
tar -czf "$tmp" --warning=no-file-changed -C "$(dirname "$SOURCE")" "$(basename "$SOURCE")" || [ $? -eq 1 ]
mv "$tmp" "$TARGET/probe-data-$stamp.tgz"

# Keep the newest KEEP, delete the rest — by name, which is by date.
ls -1 "$TARGET"/probe-data-*.tgz 2>/dev/null | sort | head -n -"$KEEP" | while read -r old; do
  rm -f "$old"
done

echo "backed up $(du -h "$TARGET/probe-data-$stamp.tgz" | cut -f1) -> $TARGET/probe-data-$stamp.tgz ($(ls -1 "$TARGET"/probe-data-*.tgz | wc -l | tr -d ' ') kept)"
