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
#   PATROL_PRESCAN         a command run before each fire. Empty = fire every
#                          tick, which is what this did before the knob existed.
#                          See the three outcomes at the call site below.
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

SNAPSHOT="${CONTRACT_SNAPSHOT:-/tmp/patrol-timer-snapshot.json}"

check_contract() {
  # A node's brief makes promises about its own state that only a before/after
  # comparison can check — see scripts/assert_node_contract.py for why three
  # stateless formulations all passed on the real defect.
  local since out
  since="$(cat "$SNAPSHOT.at" 2>/dev/null || echo 0)"
  [ -n "${CONTRACT_LEDGER_URL:-}" ] || return 0
  curl -sf ${CONTRACT_READ_TOKEN:+-H "X-Read-Token: $CONTRACT_READ_TOKEN"} \
    "$CONTRACT_LEDGER_URL" -o /tmp/patrol-timer-ledger.json 2>/dev/null || {
      log "contract check skipped: ledger unreadable"; return 0; }
  # Explicit path, not derived from $HERE: this script is mounted at /patrols
  # and the checker lives in the repo's scripts/, which is a different mount.
  # CONTRACT_CURSORS points at the reader's own state where a node no longer
  # keeps both cursors in one file — see the checker's header for which promises
  # that split changes. Unset for a node that does; the checker handles both.
  if out=$(python3 "${CONTRACT_CHECKER:-/scripts/assert_node_contract.py}" \
        --before "$SNAPSHOT" --after "$CONTRACT_STATE" \
        --ledger /tmp/patrol-timer-ledger.json \
        ${CONTRACT_CURSORS:+--cursors "$CONTRACT_CURSORS"} \
        --since "$since" --source "${CONTRACT_SOURCE:-watch}" 2>&1); then
    return 0
  fi
  log "CONTRACT VIOLATION: $(printf '%s' "$out" | grep FAIL | head -2 | tr '\n' ' ')"
  # The violation travels as a signal, posted BY THE TIMER — deliberately not
  # through the node whose failure it is reporting. `low` on purpose: this is a
  # new detector with no track record, and a new detector that pages somebody on
  # its first false positive is a detector that gets switched off. Raise it once
  # a week of real rounds says the rate is worth waking up for.
  [ -n "${CONTRACT_SIGNAL_POSTER:-}" ] || return 0
  printf '%s' "$(python3 -c "
import json,sys
out=sys.stdin.read()
fails=[l.strip()[6:] for l in out.splitlines() if l.strip().startswith('FAIL')]
print(json.dumps({
  'title': '⚠️ 盯守器违约：' + (fails[0].split('—')[0].strip() if fails else 'contract broken'),
  'detail': '上一轮没有兑现 brief 里的承诺。\n\n' + '\n'.join('- '+f for f in fails),
  'origin': 'patrol-timer / contract',
  'level': 'low',
  'kind': 'note',
}, ensure_ascii=False))
" <<< "$out")" | python3 "$CONTRACT_SIGNAL_POSTER" >/dev/null 2>&1 \
    && log "violation posted as a signal" || log "violation signal FAILED to post"
}

log "patrol-timer up: every ${EVERY}m, hours=[${HOURS:-all}] days=[${DAYS:-all}] tz=${TZ:-system}, brief=$BRIEF"
[ -n "${CONTRACT_STATE:-}" ] && log "contract check on: state=$CONTRACT_STATE snapshot=$SNAPSHOT"
[ -r "$BRIEF" ] || log "WARNING: $BRIEF is not readable — every tick will fail until it is"

while :; do
  # Sleep to the next wall-clock multiple, so restarts do not shift the grid and
  # two containers cannot drift into firing at different minutes.
  now_min=$(date +%-M); now_sec=$(date +%-S)
  next=$(( EVERY - (now_min % EVERY) ))
  sleep $(( next * 60 - now_sec ))

  dow=$(date +%u); hour=$(date +%-H)
  if ! in_range "$dow" "$DAYS" || ! in_range "$hour" "$HOURS"; then
    # Logged, not silent. The header promises one line per tick so that
    # `docker logs` answers "is it alive"; a weekend of nothing at all is the
    # same bytes as a hung loop, and it was a whole weekend before anyone asked.
    log "outside hours/days, tick skipped"
    continue
  fi

  # THE PREVIOUS ROUND'S CONTRACT, checked before firing the next one.
  #
  # Here rather than after the fire because patrol.sh returns as soon as the
  # event is accepted — the round itself runs for minutes afterwards, so a timer
  # cannot wait for the one it just started. By the time the next tick comes
  # round, the last one has long settled.
  #
  # Off unless CONTRACT_STATE is set, so the timer stays useful to a patrol that
  # has no state file to promise anything about.
  if [ -n "${CONTRACT_STATE:-}" ] && [ -f "$SNAPSHOT" ]; then
    check_contract || true
  fi
  # A cheap deterministic pass before the expensive one. Three outcomes, and
  # the first is the whole reason this knob exists:
  #
  #   exit 0, no output   nothing to do. Skip the round entirely — no event, no
  #                       model, no bill. A watcher whose quiet rounds cost the
  #                       same as its busy ones is paying a model to discover
  #                       there was nothing to discover, which on the deployment
  #                       this was written for was $0.25-0.95 per empty round.
  #   exit 0, output      what it found, appended to the brief. The model reads
  #                       findings instead of instructions for how to find them,
  #                       and does only the part that needs judging.
  #   non-zero            fire ANYWAY, carrying the failure. A prescan that
  #                       broke and a prescan that found nothing look identical
  #                       from here, and only one of them is a quiet day. The
  #                       same rule the briefs state for their own sources.
  #
  # The composed body is a temp file rather than an edit to the brief: the brief
  # is mounted read-only for the reason documented beside its volume, and a
  # findings section that accumulated across rounds would be the worst of both.
  BODY="$BRIEF"
  if [ -n "${PATROL_PRESCAN:-}" ]; then
    scan_rc=0
    # bash -c, not eval: the command is configuration and runs as itself,
    # without reaching into this shell's variables or traps.
    scan_out="$(bash -c "$PATROL_PRESCAN" 2>&1)" || scan_rc=$?
    if [ "$scan_rc" -eq 0 ] && [ -z "$scan_out" ]; then
      log "prescan: nothing to do, round skipped"
      continue
    fi
    BODY="$(mktemp "${TMPDIR:-/tmp}/patrol-body.XXXXXX")"
    if [ "$scan_rc" -ne 0 ]; then
      log "prescan FAILED (rc=$scan_rc) — firing anyway so the failure gets reported"
      printf '%s\n\n---\n\n## ⚠️ PRESCAN FAILED (rc=%s)\n\n```\n%s\n```\n' \
        "$(cat "$BRIEF")" "$scan_rc" "$scan_out" > "$BODY"
    else
      printf '%s\n\n---\n\n%s\n' "$(cat "$BRIEF")" "$scan_out" > "$BODY"
    fi
  fi

  if [ -n "${CONTRACT_STATE:-}" ] && [ -r "$CONTRACT_STATE" ]; then
    # Snapshot BEFORE firing: this is what the round about to start will be
    # measured against. After the prescan, so a skipped round leaves the
    # previous snapshot in place rather than measuring a round that never ran.
    cp "$CONTRACT_STATE" "$SNAPSHOT" 2>/dev/null || log "WARNING: could not snapshot $CONTRACT_STATE"
    SNAPSHOT_AT=$(date +%s)
    printf '%s' "$SNAPSHOT_AT" > "$SNAPSHOT.at"
  fi

  # Never `set -e` around this: one failed round must not stop the clock. The
  # round's own failure travels as a signal (the brief owes the operator that),
  # and the exit code lands in this log either way.
  if out=$(bash "$HERE/patrol.sh" "$BODY" "$TITLE" 2>&1); then
    log "fired: $out"
  else
    log "FAILED (rc=$?): $out"
  fi
  [ "$BODY" = "$BRIEF" ] || rm -f "$BODY"
done
