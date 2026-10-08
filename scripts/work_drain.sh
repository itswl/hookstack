#!/usr/bin/env bash
# Wait for the work stack's runs in flight to finish before a recreate.
#
# Since 2026-10-08 a recreate no longer loses a run: a graceful stop leaves it
# for the next boot to continue, the way a crash does. Continuing still costs a
# resume turn and whatever the interrupted turn had spent, and a run near its end
# costs neither if the recreate waits for it. So this waits, up to
# DRAIN_MAX_SECONDS (default 900), for every running probe to report nothing in
# flight, then exits 0. At the deadline it names what is still running and exits
# 1, and the caller decides; recreating anyway is safe, only dearer.
#
#   scripts/work_drain.sh && docker compose -p hookstack-work --env-file .env \
#     -f deploy/docker-compose.work.yml up -d --build
#
# A run that starts after the last look rides the recreate and is continued at
# the next boot. Each probe is read through the doors container's published port
# (8088 planner, 8089 watcher, 8090 work runner) with the token from that probe's
# own environment, so nothing is typed into a shell history. A board that does
# not answer counts as busy: an unknown is not an empty board.
set -euo pipefail

max="${DRAIN_MAX_SECONDS:-900}"
deadline=$(($(date +%s) + max))
nodes=("work-probe-plan:8088" "work-probe-watch:8089" "work-probe-work:8090")

token() { docker inspect "$1" --format '{{range .Config.Env}}{{println .}}{{end}}' | sed -n 's/^HOOKPROBE_TOKEN=//p' | head -1; }

while :; do
  busy=()
  for node in "${nodes[@]}"; do
    name="${node%%:*}" port="${node##*:}"
    # A stopped probe has nothing in flight; one that is not there at all is not ours.
    [ "$(docker inspect -f '{{.State.Running}}' "$name" 2>/dev/null || true)" = "true" ] || continue
    if ! body="$(curl -fsS -m 10 -H "Authorization: Bearer $(token "$name")" "http://127.0.0.1:$port/v1/runs?limit=50")"; then
      busy+=("$name: board did not answer")
      continue
    fi
    running="$(printf '%s' "$body" | python3 -c \
      'import json, sys; print(" ".join(r["session_key"] for r in json.load(sys.stdin) if r.get("status") == "running"))')"
    [ -z "$running" ] || busy+=("$name: $running")
  done
  if [ ${#busy[@]} -eq 0 ]; then
    echo "drained: no run in flight"
    exit 0
  fi
  if [ "$(date +%s)" -ge "$deadline" ]; then
    echo "still in flight after ${max}s:" >&2
    printf '  %s\n' "${busy[@]}" >&2
    exit 1
  fi
  echo "waiting: ${busy[*]}"
  sleep 15
done
