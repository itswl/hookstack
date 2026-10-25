#!/usr/bin/env bash
# The week on one page, on a timer.
#
# scripts/cost_report.py joins the three consoles into the governance page, and
# until 2026-09-15 somebody had to remember to run it — which meant it was run
# by an agent mid-session and read by nobody on a Monday. This runs it from
# INSIDE the compose network, writes the page to a dated file, and posts its
# headline lines through the pipe's watch door, which is the door that reaches
# the operator where the cards already are. `low`, so the investigator declines
# it by level and no run is funded for a report.
#
# Two deployment shapes, because there are two:
#
#   HOOKSTACK_DEPLOY=work WEEKLY_PAGE_DIR=/where/pages/go scripts/weekly_page.sh
#   HOOKSTACK_DEPLOY=shadow ... scripts/weekly_page.sh   # the retired shape
#
# `work` is the one running today. It has no judge, and its three probes are
# named individually in the compose; the page reads the pipe and all three
# investigators, and the judge section says it was not configured rather than
# printing a zero. `shadow` keeps the old project, compose and endpoints.
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

DEPLOY="${HOOKSTACK_DEPLOY:-shadow}"
case "$DEPLOY" in
  shadow)
    RELAY_TOKEN="$(token hookrelay HOOKRELAY_READ_TOKEN || true)"
    JUDGE_TOKEN="$(token hookjudge HOOKJUDGE_READ_TOKEN || true)"
    PROBE_TOKEN="$(token hookprobe HOOKPROBE_TOKEN || true)"
    run=(docker compose -p hookstack-shadow --env-file .env -f deploy/docker-compose.shadow.yml
      run --rm --no-deps -T -v "$ROOT/scripts:/scripts:ro")
    post_service=hookjudge
    "${run[@]}" -e "HOOKRELAY_READ_TOKEN=$RELAY_TOKEN" -e "HOOKJUDGE_READ_TOKEN=$JUDGE_TOKEN" \
      -e "HOOKPROBE_TOKEN=$PROBE_TOKEN" \
      hookjudge python3 /scripts/cost_report.py --utc-offset "$(date +%z)" \
      --relay http://hookrelay:8100 --judge http://hookjudge:8200 --probe http://hookprobe:8088 > "$page"
    ;;
  work)
    RELAY_TOKEN="$(token work-hookrelay HOOKRELAY_READ_TOKEN || true)"
    PROBE_TOKEN="$(token work-probe-plan HOOKPROBE_TOKEN || true)"
    [ -n "$RELAY_TOKEN" ] || { echo "work: no HOOKRELAY_READ_TOKEN on work-hookrelay" >&2; exit 1; }
    [ -n "$PROBE_TOKEN" ] || { echo "work: no HOOKPROBE_TOKEN on work-probe-plan" >&2; exit 1; }
    run=(docker compose -p hookstack-work --env-file .env -f deploy/docker-compose.work.yml
      run --rm --no-deps -T -v "$ROOT/scripts:/scripts:ro")
    post_service=hookrelay
    "${run[@]}" -e "HOOKRELAY_READ_TOKEN=$RELAY_TOKEN" -e "HOOKPROBE_TOKEN=$PROBE_TOKEN" \
      hookrelay python3 /scripts/cost_report.py --utc-offset "$(date +%z)" \
      --relay http://hookrelay:8100 \
      --probe http://probe-plan:8088,http://probe-watch:8088,http://probe-work:8088 > "$page"
    ;;
  *)
    echo "unknown HOOKSTACK_DEPLOY=$DEPLOY (expected shadow or work)" >&2
    exit 2
    ;;
esac
echo "wrote $page ($(wc -l < "$page") lines)"

# The headline: the lines a person decides from, not the whole page.
signal="$(python3 - "$page" "$stamp" <<'PY'
import json, re, sys
page, stamp = open(sys.argv[1], encoding="utf-8").read(), sys.argv[2]
keep = ("Reached a person", "Pressed by a person", "Measured", "Priced", "Golden gate", "Kept from a person",
        "Loudest condition", "Counterfactual", "Declined at the door", "Budget", "The posture refused", "Worth")
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
  "$post_service" python3 /scripts/post_watch_signal.py
