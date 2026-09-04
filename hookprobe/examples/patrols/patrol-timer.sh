#!/usr/bin/env bash
# A patrol timer that ships WITH the deployment instead of living in the host's
# crontab. Same brief, same patrol.sh, same door — only the clock moved.
#
#   patrol-timer.sh <brief.md> ["Title"]
#
# Why this exists, from a failure rather than a preference: the host crontab
# version could not read its own brief. macOS keeps ~/Documents behind TCC, and
# `cron` is not one of the processes allowed through it, so every fire logged
#   bash: .../patrol.sh: Operation not permitted
# and nothing else in the system noticed — the pipe had no delivery to fail, the
# investigator was healthy and idle, and the chat was quiet. A watcher that was
# never triggered and a quiet afternoon look identical from the outside.
#
# The two host-side fixes both cost something: granting /usr/sbin/cron Full Disk
# Access widens every OTHER cron job on that machine, and copying the brief and
# patrol.sh somewhere unprotected creates a second copy that drifts. A container
# has neither problem: it carries its own filesystem, and `docker compose up`
# starts the timer with the thing it triggers.
#
# It is a SEPARATE service from the investigator on purpose. A timer inside the
# container it triggers cannot tell you "the timer died" apart from "the watcher
# died", and those want different fixes. This one logs every tick, so `docker
# logs` answers "is it alive" without anybody guessing.
#
# Env (all optional except the secret patrol.sh itself needs):
#   PATROL_EVERY_MINUTES   default 20. Fires on wall-clock multiples, so 20
#                          means :00 :20 :40 rather than "20 minutes after
#                          whenever this container happened to start".
#   PATROL_HOURS           e.g. 9-19. Empty = every hour.
#   PATROL_DAYS            e.g. 1-5 for Mon-Fri (1=Mon). Empty = every day.
#   TZ                     the operator's zone, not UTC — the hours above and
#                          the brief's own window check must agree about what
#                          time it is.
#
# The hour/day gate here is an ECONOMY, never the authority. The brief decides
# whether a round does anything (it runs `date` first and answers [SILENT]
# outside its window); this only avoids paying for near-empty rounds all night.
# Keep it wider than the brief's window, never narrower.
set -uo pipefail

BRIEF="${1:?usage: patrol-timer.sh <brief.md> [title]}"
TITLE="${2:-Patrol}"
EVERY="${PATROL_EVERY_MINUTES:-20}"
HOURS="${PATROL_HOURS:-}"
DAYS="${PATROL_DAYS:-}"
HERE="$(cd "$(dirname "$0")" && pwd)"

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$*"; }

in_range() {  # in_range <value> <spec>  — "" matches, "9-19" and "1-5" are ranges
  local v="$1" spec="$2"
  [ -z "$spec" ] && return 0
  local lo="${spec%%-*}" hi="${spec##*-}"
  [ "$v" -ge "$lo" ] && [ "$v" -le "$hi" ]
}

log "patrol-timer up: every ${EVERY}m, hours=[${HOURS:-all}] days=[${DAYS:-all}] tz=${TZ:-system}, brief=$BRIEF"
[ -r "$BRIEF" ] || log "WARNING: $BRIEF is not readable — every tick will fail until it is"

while :; do
  # Sleep to the next wall-clock multiple, so restarts do not shift the grid and
  # two containers cannot drift into firing at different minutes.
  now_min=$(date +%-M); now_sec=$(date +%-S)
  next=$(( EVERY - (now_min % EVERY) ))
  sleep $(( next * 60 - now_sec ))

  dow=$(date +%u); hour=$(date +%-H)
  if ! in_range "$dow" "$DAYS" || ! in_range "$hour" "$HOURS"; then
    continue
  fi

  # Never `set -e` around this: one failed round must not stop the clock. The
  # round's own failure travels as a signal (the brief owes the operator that),
  # and the exit code lands in this log either way.
  if out=$(bash "$HERE/patrol.sh" "$BRIEF" "$TITLE" 2>&1); then
    log "fired: $out"
  else
    log "FAILED (rc=$?): $out"
  fi
done
