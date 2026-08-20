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
# Env:
#   HOOKRELAY_URL             default http://127.0.0.1:8100
#   HOOKRELAY_SOURCE          default inbound — the front door's source name
#   HOOKRELAY_INBOUND_SECRET  that source's `secret:` in the pipe's config.
#                             Empty = post unsigned, which the pipe accepts
#                             only for a source configured without a secret.
#   PATROL_ENV                default prod — lands in fields.env
#   PATROL_STATE              default alerting — level_map turns it into `high`,
#                             which is what gets the event past the probe's
#                             HOOKPROBE_ESCALATE_LEVELS gate. A patrol posted at
#                             a level outside that gate is acknowledged and
#                             never investigated.
set -euo pipefail

BRIEF="${1:?usage: patrol.sh <brief.md> [\"Title\"]}"
TITLE="${2:-}"
[ -r "$BRIEF" ] || { echo "patrol: cannot read brief $BRIEF" >&2; exit 1; }

BASE="${HOOKRELAY_URL:-http://127.0.0.1:8100}"
SOURCE="${HOOKRELAY_SOURCE:-inbound}"
URL="$BASE/hook/$SOURCE"

# The event door caps the alert body at 4000 bytes and says so in the prompt
# where it cut. A brief that overruns is not an error the pipe reports — it is
# a prompt whose last instruction silently never reached the model, so refuse
# here instead, while there is somebody to tell.
size="$(wc -c < "$BRIEF" | tr -d ' ')"
if [ "$size" -gt 4000 ]; then
  echo "patrol: $BRIEF is $size bytes; the event door truncates the body at 4000" >&2
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
body = json.dumps(
    {
        "title": title,
        "message": brief.read_text(encoding="utf-8").strip(),
        "state": os.environ.get("PATROL_STATE", "alerting"),
        "env": os.environ.get("PATROL_ENV", "prod"),
    },
    ensure_ascii=False,
).encode("utf-8")
pathlib.Path(os.environ["BODY_OUT"]).write_bytes(body)

stamp = str(int(time.time()))
secret = os.environ.get("HOOKRELAY_INBOUND_SECRET", "")
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
curl "${args[@]}"
echo
