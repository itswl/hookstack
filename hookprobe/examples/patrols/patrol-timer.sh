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

STAMP="${WATCH_SIGNED_STAMP:-/tmp/patrol-timer-signed.at}"

check_signed() {
  # Every watch signal in the pipe came through the signer — see
  # scripts/assert_watch_signed.py. Checked once per tick over what arrived
  # since the last check; a miss travels as a `low` signal posted BY THE TIMER,
  # which signs from its own file and never passes the signer. The first tick
  # after a deploy only sets the stamp: the pipe's ledger reaches back before
  # the signer existed, and that history is not a finding.
  local since now out
  [ -n "${WATCH_SIGNED_LEDGER_URL:-}" ] && [ -n "${WATCH_SIGNED_SIGNER_LEDGER:-}" ] || return 0
  now=$(date +%s)
  if ! since="$(cat "$STAMP" 2>/dev/null)"; then
    printf '%s' "$now" > "$STAMP"; return 0
  fi
  curl -sf ${WATCH_SIGNED_READ_TOKEN:+-H "X-Read-Token: $WATCH_SIGNED_READ_TOKEN"} \
    "$WATCH_SIGNED_LEDGER_URL" -o /tmp/patrol-timer-ledger.json 2>/dev/null || {
      log "signed check skipped: ledger unreadable"; return 0; }
  if out=$(python3 /scripts/assert_watch_signed.py --ledger /tmp/patrol-timer-ledger.json \
        --signer-ledger "$WATCH_SIGNED_SIGNER_LEDGER" --since "$since" \
        --source "${WATCH_SIGNED_SOURCE:-watch}" 2>&1); then
    printf '%s' "$now" > "$STAMP"; return 0
  fi
  printf '%s' "$now" > "$STAMP"
  log "UNSIGNED SIGNAL: $(printf '%s' "$out" | grep FAIL | head -2 | tr '\n' ' ')"
  # `low` on purpose: a detector that pages somebody on its first false
  # positive is a detector that gets switched off.
  [ -n "${WATCH_SIGNED_POSTER:-}" ] || return 0
  printf '%s' "$(python3 -c "
import json,sys
out=sys.stdin.read()
fails=[l.strip()[6:] for l in out.splitlines() if l.strip().startswith('FAIL')]
print(json.dumps({
  'title': '⚠️ 有信号绕过了签名器：' + (fails[0].split('—')[0].strip() if fails else 'unsigned'),
  'detail': '管道的 watch 门收到了签名器账本里没有的信号。\n\n' + '\n'.join('- '+f for f in fails),
  'origin': 'patrol-timer / signed',
  'level': 'low',
  'kind': 'note',
}, ensure_ascii=False))
" <<< "$out")" | python3 "$WATCH_SIGNED_POSTER" >/dev/null 2>&1 \
    && log "unsigned-signal finding posted as a signal" || log "unsigned-signal finding FAILED to post"
}

log "patrol-timer up: every ${EVERY}m, hours=[${HOURS:-all}] days=[${DAYS:-all}] tz=${TZ:-system}, brief=$BRIEF"
[ -n "${WATCH_SIGNED_SIGNER_LEDGER:-}" ] && log "signed check on: every watch signal must be in $WATCH_SIGNED_SIGNER_LEDGER"
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

  # Before the fire, not after: patrol.sh returns as soon as the event is
  # accepted and the round runs for minutes afterwards, so what is checked here
  # is what the LAST round left in the pipe. Off unless the compose names the
  # two ledgers, so the timer stays useful to a patrol with no signer.
  check_signed || true
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
  # A brief may carry `{{NAME}}` placeholders, filled from the environment. The
  # judging rules are the same for every deployment and belong in the repository;
  # WHO to interrupt and whose word carries weight are real people's names, and
  # those belong in the deployment's .env with every other identifier.
  #
  # An unresolved placeholder REFUSES the round rather than rendering a hole.
  # A brief that reads "判断哪些值得打断 " with the name missing still parses,
  # still costs a model call, and quietly judges everything as unimportant —
  # which from outside is indistinguishable from a quiet day. Refusing is loud:
  # the log says which variable, and the pipe's absence alarm fires if it lasts.
  BRIEF_RENDERED=""
  BRIEF_NOW="$BRIEF"
  if grep -q '{{[A-Z_][A-Z0-9_]*}}' "$BRIEF" 2>/dev/null; then
    BRIEF_RENDERED="$(mktemp "${TMPDIR:-/tmp}/patrol-brief.XXXXXX")"
    if ! BRIEF_IN="$BRIEF" BRIEF_OUT="$BRIEF_RENDERED" python3 -c '
import os, re, sys
text = open(os.environ["BRIEF_IN"], encoding="utf-8").read()
missing = sorted({m for m in re.findall(r"{{([A-Z_][A-Z0-9_]*)}}", text) if not os.environ.get(m, "").strip()})
if missing:
    print(" ".join(missing), file=sys.stderr)
    raise SystemExit(1)
open(os.environ["BRIEF_OUT"], "w", encoding="utf-8").write(
    re.sub(r"{{([A-Z_][A-Z0-9_]*)}}", lambda m: os.environ[m.group(1)], text))
' 2>/tmp/patrol-brief-err; then
      log "brief REFUSED: $BRIEF needs $(cat /tmp/patrol-brief-err) in the environment — round skipped"
      rm -f "$BRIEF_RENDERED" /tmp/patrol-brief-err
      continue
    fi
    rm -f /tmp/patrol-brief-err
    BRIEF_NOW="$BRIEF_RENDERED"
  fi

  BODY="$BRIEF_NOW"
  if [ -n "${PATROL_PRESCAN:-}" ]; then
    scan_rc=0
    # bash -c, not eval: the command is configuration and runs as itself,
    # without reaching into this shell's variables or traps.
    scan_out="$(bash -c "$PATROL_PRESCAN" 2>&1)" || scan_rc=$?
    if [ "$scan_rc" -eq 0 ] && [ -z "$scan_out" ]; then
      # A quiet round is not a stopped clock, and the door cannot tell the two
      # apart unless it hears something. The work pipe alarms when watch-due
      # has been silent for 25 minutes; skipping a round here posted nothing,
      # so every two quiet rounds in a row raised "watch-due has said nothing
      # for 25 minutes" in the operator's chat — 38 of them in 7.7 days, 37 of
      # which were exactly that (2026-09-23). A heartbeat says "alive, nothing
      # to judge" to the same door, and the door's own config drops it before
      # any route can fund a run. A heartbeat that fails is logged and the
      # round is still skipped: missing one beat is what the alarm is for.
      beat_body="$(mktemp "${TMPDIR:-/tmp}/patrol-beat.XXXXXX")"
      printf '%s\n' "Quiet round: the prescan found nothing to judge, so no model was asked." > "$beat_body"
      if beat_out=$(PATROL_BEAT=yes PATROL_STATE=ok bash "$HERE/patrol.sh" "$beat_body" "$TITLE: quiet round" 2>&1); then
        log "prescan: nothing to do, round skipped (heartbeat: ${beat_out:0:100})"
      else
        log "prescan: nothing to do, round skipped — heartbeat FAILED (rc=$?): ${beat_out:0:200}"
      fi
      rm -f "$beat_body"
      continue
    fi
    BODY="$(mktemp "${TMPDIR:-/tmp}/patrol-body.XXXXXX")"
    if [ "$scan_rc" -ne 0 ]; then
      log "prescan FAILED (rc=$scan_rc) — firing anyway so the failure gets reported"
      printf '%s\n\n---\n\n## ⚠️ PRESCAN FAILED (rc=%s)\n\n```\n%s\n```\n' \
        "$(cat "$BRIEF_NOW")" "$scan_rc" "$scan_out" > "$BODY"
    else
      printf '%s\n\n---\n\n%s\n' "$(cat "$BRIEF_NOW")" "$scan_out" > "$BODY"
    fi
  fi

  if out=$(bash "$HERE/patrol.sh" "$BODY" "$TITLE" 2>&1); then
    log "fired: $out"
  else
    log "FAILED (rc=$?): $out"
  fi
  [ "$BODY" = "$BRIEF_NOW" ] || rm -f "$BODY"
  [ -z "$BRIEF_RENDERED" ] || rm -f "$BRIEF_RENDERED"
done
