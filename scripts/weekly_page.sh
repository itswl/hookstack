#!/usr/bin/env bash
# The week on one page, on a timer.
#
# scripts/cost_report.py joins the three consoles into the governance page, and
# until 2026-09-15 somebody had to remember to run it — which meant it was run
# by an agent mid-session and read by nobody on a Monday. This runs it from
# INSIDE the compose network (the pipe publishes no host port, so a host-side
# run always printed the pipe section as unread), writes the page to a dated
# file, and posts its headline lines through the pipe's watch door, which is
# the door that reaches the operator where the cards already are. `low`, so the
# investigator declines it by level and no run is funded for a report.
#
#   WEEKLY_PAGE_DIR=/where/pages/go scripts/weekly_page.sh
#
# Tokens are read from the running containers' environment, not typed into a
# crontab line; only the door secret is copied into a file the container can read.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
OUT_DIR="${WEEKLY_PAGE_DIR:-$ROOT/reports}"
mkdir -p "$OUT_DIR"
stamp="$(date -u +%Y-%m-%d)"
page="$OUT_DIR/$stamp.md"

token() { docker inspect "$1" --format '{{range .Config.Env}}{{println .}}{{end}}' | sed -n "s/^$2=//p" | head -1; }
RELAY_TOKEN="$(token hookrelay HOOKRELAY_READ_TOKEN || true)"
JUDGE_TOKEN="$(token hookjudge HOOKJUDGE_READ_TOKEN || true)"
PROBE_TOKEN="$(token hookprobe HOOKPROBE_TOKEN || true)"

run=(docker compose -p hookstack-shadow --env-file .env -f deploy/docker-compose.shadow.yml
  run --rm --no-deps -T -v "$ROOT/scripts:/scripts:ro")

"${run[@]}" -e "HOOKRELAY_READ_TOKEN=$RELAY_TOKEN" -e "HOOKJUDGE_READ_TOKEN=$JUDGE_TOKEN" -e "HOOKPROBE_TOKEN=$PROBE_TOKEN" \
  hookjudge python3 /scripts/cost_report.py \
  --relay http://hookrelay:8100 --judge http://hookjudge:8200 --probe http://hookprobe:8088 > "$page"
echo "wrote $page ($(wc -l < "$page") lines)"

# The headline: the lines a person decides from, not the whole page.
signal="$(python3 - "$page" "$stamp" <<'PY'
import json, re, sys
page, stamp = open(sys.argv[1], encoding="utf-8").read(), sys.argv[2]
keep = ("Measured", "Priced", "Golden gate", "Kept from a person", "Loudest condition", "Counterfactual",
        "Declined at the door", "Budget", "The posture refused", "Worth")
lines, section = [], ""
for raw in page.splitlines():
    if raw.startswith("## "):
        section = raw[3:].strip()
        continue
    if raw.startswith("- **") and any(raw.startswith(f"- **{k}") for k in keep):
        lines.append(f"[{section}] {re.sub(r'\*\*', '', raw[2:])}")
detail = "\n".join(lines)[:1800] or "the page was written but had no headline lines"
print(json.dumps({"title": f"Weekly page · {stamp}", "detail": detail, "level": "low",
                  "origin": "weekly-page", "kind": "report"}, ensure_ascii=False))
PY
)"
# Only the door secret crosses into the container, in a file it can read: the
# deployment .env is mode 600 and holds every other secret this stack has.
secret_file="$(mktemp)"
trap 'rm -f "$secret_file"' EXIT
grep -E '^WATCH_INGEST_SECRET=' .env > "$secret_file"
chmod 644 "$secret_file"
printf '%s' "$signal" | "${run[@]}" -v "$secret_file:/deployment/.env:ro" \
  -e HOOKSTACK_ENV_FILE=/deployment/.env \
  -e HOOKSTACK_WATCH_DOOR=http://hookrelay:8100/hook/watch \
  hookjudge python3 /scripts/post_watch_signal.py
