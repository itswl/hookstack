#!/usr/bin/env bash
# The morning card: what needs you, where the cards already are.
#
# Runs on the host that runs the work deployment (the pipe publishes 127.0.0.1:8100
# there), reads the pipe's two feeds with its read token, and posts ONE signal
# through the pipe's watch door — `low`, `kind: report`, so it becomes a card in
# the watch group and funds no run. A crontab line supplies the clock:
#
#   35 9 * * 1-5 cd $HOME/Documents/hookstack && scripts/needs_you.sh >> $HOME/Library/Logs/hookstack-needs-you.log 2>&1
#
# The read token and the door secret are read from the deployment .env (mode 600)
# by the two scripts; nothing is typed into the crontab line.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
RELAY="${HOOKRELAY_URL:-http://127.0.0.1:8100}"
token="$(sed -n 's/^WORK_READ_TOKEN=//p; s/^HOOKRELAY_READ_TOKEN=//p' .env | head -1 | tr -d '"')"
HOOKRELAY_READ_TOKEN="$token" python3 scripts/needs_you.py --relay "$RELAY" "$@" | python3 scripts/post_watch_signal.py
echo "$(date '+%Y-%m-%d %H:%M:%S') morning card posted"
