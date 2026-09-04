#!/usr/bin/env bash
# Post a patrol brief to hookrelay's front door, signed with the family's
# timestamped HMAC. The crontab sibling of scripts/backup_probe_data.sh.
#
#   patrol.sh <brief.md> ["Title"]
#
# Install (host crontab) — see README.md beside this file for the full lines:
#   0 9 * * 1 HOOKRELAY_INBOUND_SECRET=... /opt/.../patrol.sh /opt/.../patrols/weekly-attention-review.md
#
# Why a script and not one curl line in the crontab: the brief is read HERE, at
# send time, from a file on the host. Edit the file and the next patrol carries
# the new text — no image rebuild, no container restart. That is the same
# property the investigator's own CLAUDE.md and system-prompt.md have (read
# fresh from the volume at every run), applied to the one prompt that does not
# live on the volume: the task.
#
# Two targets, because not every deployment routes the pipe to the investigator.
#
#   PATROL_TARGET=relay  (default)  through hookrelay's front door, as above.
#   PATROL_TARGET=probe             straight to the investigator's own door.
#
# `relay` is the design: the event is accounted, deduped and silenced like any
# other, and nothing in any service knows what a patrol is. Prefer it.
#
# `probe` exists because a deployment can deliberately have no route from the
# pipe to the investigator, and this one does — deploy/shadow.yaml lists "probe
# escalation" under what is ABSENT, on the grounds that a second door onto the
# same alert runs would double the model bill. That reasoning is about alerts,
# not about a scheduled self-review with no alert behind it; but widening the
# shadow's reach to carry a patrol would trade a narrow config for a cron job's
# convenience, which is the wrong way round. So the patrol goes in the way the
# platform's own deep-analysis leg already goes in: POST /hooks/agent.
#
# What that mode gives up, stated plainly: the pipe's accounting, dedup and
# silence. A patrol is scheduled, singular and wanted, so all three are close to
# no-ops for it — but the run does not appear in the pipe's ledger, and if you
# are reconciling spend, that is where the difference will show.
#
# Env:
#   PATROL_TARGET             relay (default) | probe
#   HOOKRELAY_URL             default http://127.0.0.1:8100
#   HOOKRELAY_SOURCE          default inbound — the front door's source name
#   HOOKRELAY_INBOUND_SECRET  that source's `secret:` in the pipe's config.
#                             Empty = post unsigned, which the pipe accepts
#                             only for a source configured without a secret.
#   HOOKPROBE_URL             default http://127.0.0.1:8088   (probe target)
#   HOOKPROBE_TOKEN           the investigator's bearer token (probe target).
#                             Empty is only right where the probe itself runs
#                             unauthenticated on a private network.
#   PATROL_SESSION_KEY        override the run key (probe target). The default,
#                             patrol:<brief>:<date>, makes a duplicate fire free
#                             — but the investigator is idempotent per key for
#                             FINISHED runs too, so after a failure that key is
#                             spent for the day. Fix the cause, then retry with
#                             an explicit key.
#   PATROL_ENV                default prod — lands in fields.env
#   PATROL_STATE              default alerting — level_map turns it into `high`,
#                             which is what gets the event past the probe's
#                             HOOKPROBE_ESCALATE_LEVELS gate. A patrol posted at
#                             a level outside that gate is acknowledged and
#                             never investigated. Ignored by the probe target,
#                             which has no level gate in front of it.
set -euo pipefail

BRIEF="${1:?usage: patrol.sh <brief.md> [\"Title\"]}"
TITLE="${2:-}"
[ -r "$BRIEF" ] || { echo "patrol: cannot read brief $BRIEF" >&2; exit 1; }

TARGET="${PATROL_TARGET:-relay}"
case "$TARGET" in
  relay) URL="${HOOKRELAY_URL:-http://127.0.0.1:8100}/hook/${HOOKRELAY_SOURCE:-inbound}" ;;
  probe) URL="${HOOKPROBE_URL:-http://127.0.0.1:8088}/hooks/agent" ;;
  *) echo "patrol: PATROL_TARGET must be relay or probe, not '$TARGET'" >&2; exit 1 ;;
esac

# The event door caps the body and says so in the prompt where it cut. A brief
# that overruns is not an error the pipe reports — it is a prompt whose last
# instruction silently never reached the model, so refuse here instead, while
# there is somebody to tell.
#
# The cap depends on how the DOOR will read this, and the door decides that from
# `fields.kind` in the pipe's source config, which this script cannot see: an
# alert body is capped at 4000, a `kind: brief` at 16000. So the number is an
# env knob defaulting to the stricter one. Raise it in the caller that also
# configured the door for briefs — and if you raise it here and not there, this
# stops refusing and the door starts truncating, which is the failure this check
# exists to prevent.
size="$(wc -c < "$BRIEF" | tr -d ' ')"
cap="${PATROL_BODY_MAX:-4000}"
if [ "$size" -gt "$cap" ]; then
  echo "patrol: $BRIEF is $size bytes; the event door truncates the body at $cap" >&2
  echo "        (a door configured with fields.kind: brief allows 16000 — set PATROL_BODY_MAX)" >&2
  exit 1
fi

tmp="$(mktemp "${TMPDIR:-/tmp}/patrol.XXXXXX")"
trap 'rm -f "$tmp"' EXIT

# One python3 call builds the exact bytes and signs those same bytes. Two
# separate encodings (jq for the body, openssl for the digest) is how a
# signature comes to cover a body that was never sent. The secret arrives by
# environment, never on a command line: argv is world-readable on the host and
# a cron job's is no exception.
signed="$(
  BRIEF="$BRIEF" TITLE="$TITLE" BODY_OUT="$tmp" python3 - <<'PY'
import hashlib, hmac, json, os, pathlib, time

brief = pathlib.Path(os.environ["BRIEF"])
title = os.environ.get("TITLE") or brief.stem.replace("-", " ").capitalize()
text = brief.read_text(encoding="utf-8").strip()
if os.environ.get("PATROL_TARGET", "relay") == "probe":
    # The contract on the far side: {message, sessionKey}. The title is not a
    # field here, so it goes in front of the brief rather than being dropped.
    #
    # sessionKey carries the date because `start()` is idempotent per key: a
    # duplicate cron fire — a retry, a clock adjustment, two hosts sharing a
    # crontab — returns the run already in flight instead of paying for a second
    # one. Same brief, same day, one bill.
    #
    # That idempotency covers runs which already FINISHED, so the key of a patrol
    # that FAILED is spent until tomorrow — fix the cause and the retry is a
    # no-op returning the same failure. PATROL_SESSION_KEY is how a deliberate
    # retry says it means it.
    #
    # No apostrophes in this heredoc. Bash scans a $(...) for its closing paren
    # while tracking quotes, and a lone apostrophe in here is one of them: an
    # odd count swallows the paren and the whole script stops parsing. That is how
    # this file shipped broken once. Nothing checked it; scripts/gate.sh now does.
    default_key = f"patrol:{brief.stem}:{time.strftime('%Y-%m-%d')}"
    payload = {
        "message": f"{title}\n\n{text}",
        "sessionKey": os.environ.get("PATROL_SESSION_KEY") or default_key,
        # Marks this as a run ABOUT the investigator, so the service does not
        # distil a runbook from it. The first self-review installed one called
        # patrol-self-review, and a runbook is loaded as instruction by every
        # later run: a review of the loop would have become part of the loop.
        # patrol: do not distil a runbook from a review of the investigator.
        # notify: nothing polls a patrol, so ask for the report to be returned
        # through the pipe and dressed as a card like any other.
        "_meta": {"patrol": brief.stem, "title": title, "notify": True},
    }
else:
    payload = {
        "title": title,
        "message": text,
        "state": os.environ.get("PATROL_STATE", "alerting"),
        "env": os.environ.get("PATROL_ENV", "prod"),
    }
body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
pathlib.Path(os.environ["BODY_OUT"]).write_bytes(body)

stamp = str(int(time.time()))
# The probe door authenticates with a bearer token, not this HMAC, so there is
# nothing for the relay secret to sign on that path.
secret = "" if payload.get("sessionKey") else os.environ.get("HOOKRELAY_INBOUND_SECRET", "")
# The door's preferred form: the signature covers "{timestamp}.{body}" and the
# timestamp must be within the source's max_skew (300s by default), which bounds
# how long a captured patrol stays replayable. See hookrelay/hookrelay/security.py
# for exactly what that does and does not buy.
digest = (
    hmac.new(secret.encode(), stamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    if secret
    else ""
)
print(f"{stamp}:{digest}")
PY
)"
# Split on the colon, not on whitespace: with no secret the digest is empty,
# and "stamp " vs "stamp" is the kind of difference that turns an unsigned post
# into one carrying the timestamp as its signature.
stamp="${signed%%:*}"
digest="${signed#*:}"

args=(-sS -f -X POST "$URL" -H 'content-type: application/json' --data-binary "@$tmp")
if [ -n "$digest" ]; then
  args+=(-H "X-Hook-Timestamp: $stamp" -H "X-Hook-Signature: sha256=$digest")
fi
# Header built here and not in argv above: a token on a command line is readable
# by every process on the host, and a cron job's argv is no exception.
if [ "$TARGET" = probe ] && [ -n "${HOOKPROBE_TOKEN:-}" ]; then
  args+=(-H "Authorization: Bearer $HOOKPROBE_TOKEN")
  # A patrol runs only because a person installed its cron and wrote its brief:
  # it is a standing human instruction executed on a schedule, not a rule
  # reacting to traffic. HOOKPROBE_BUDGET_GATES_AGENT_DOOR treats an undeclared
  # caller as automated and refuses it once the window is spent, which for a
  # weekly patrol means losing a week of consolidation, rulings and memory
  # suggestions — the only things that improve this service. Five bounded runs a
  # week against that is not a close call. Set PATROL_OPERATOR=0 to let the meter
  # gate them, on a deployment where the budget matters more than the loop.
  if [ "${PATROL_OPERATOR:-1}" != 0 ]; then
    args+=(-H "X-Operator: true")
  fi
fi
curl "${args[@]}"
echo
