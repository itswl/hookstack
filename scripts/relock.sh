#!/usr/bin/env bash
# Regenerate every service's requirements.lock from its OWN venv — the venv
# the gate just ran green against, which is the only resolution worth pinning.
#
# The lock is consumed as a pip constraints file (-c): requirements.txt and
# pyproject keep declaring intent with floors, the lock pins what those floors
# resolved to on the day everything passed. Docker builds install through it,
# so a bad upstream release cannot arrive silently on the next rebuild; it can
# only arrive through this script, in a diff, next to a green gate.
#
# Usage: bash scripts/relock.sh          # after a deliberate dependency bump
set -euo pipefail
cd "$(dirname "$0")/.."

for service in hookrelay hookjudge hookprobe; do
  py="$service/.venv/bin/python"
  if [ ! -x "$py" ]; then
    echo "skip $service: no venv (create it and run its gate first)" >&2
    continue
  fi
  "$py" -m pip freeze --exclude-editable | grep -v "^${service}" > "$service/requirements.lock"
  echo "$service/requirements.lock: $(wc -l < "$service/requirements.lock" | tr -d ' ') pins"
done
