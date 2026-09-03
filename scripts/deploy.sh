#!/usr/bin/env bash
# The whole deployment, one command, from the checkout it lives in:
#
#   ./scripts/deploy.sh              # pull main, rebuild both projects, read back health
#   DEPLOY_NO_PULL=1 ./scripts/deploy.sh   # rebuild what is already checked out
#
# Rollback: every successful deploy tags the images it shipped `:<name>-rb-<sha>`
# (kept: the last ROLLBACK_KEEP=3), so a bad ship recovers in seconds without a
# rebuild of an older commit:
#   docker tag hookrelay:hookrelay-rb-<oldsha> hookrelay:shadow
#   docker tag hookjudge:hookjudge-rb-<oldsha> hookjudge:shadow
#   docker compose -p hookstack-shadow --env-file .env -f deploy/docker-compose.shadow.yml up -d
#   # (hookprobe: hookprobe-rb-<oldsha> -> hookprobe:latest, then its own up -d)
# `docker images | grep -- -rb-` lists what is on hand; the sha is the commit.
#
# Codified because deploying from memory failed twice in one day, both times
# on knowledge this file now holds:
#   - compose resolves .env relative to the COMPOSE FILE's directory, not the
#     cwd, so the shadow project refuses to start ("SHADOW_INGEST_SECRET is
#     missing a value") unless --env-file names the root .env explicitly;
#   - the project names are load-bearing: a different -p creates a SECOND
#     copy of every container beside the live one instead of replacing it;
#   - `up -d --force-recreate` without --build once ran a week-old image on
#     new config and took the relay down for a minute.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [ "${DEPLOY_NO_PULL:-}" != "1" ]; then
  git pull --ff-only origin main
fi
echo "deploying $(git log --oneline -1)"

# Both projects, exactly as first created (docker inspect the containers if in
# doubt — the compose labels are the authority these names were read from).
docker compose -p hookstack-shadow --env-file .env -f deploy/docker-compose.shadow.yml build

# The judgment gate: replay the golden incidents through the judge image that
# is ABOUT to ship, before it does. The code gates (tests, lint) cannot see a
# prompt regression — only running the same alerts through the new prompt can,
# which is why this sits between build and up rather than in CI: the dataset
# holds real alert text (git-ignored by class) and the provider key lives in
# .env, so this host is the one place all three ingredients meet.
#
# It trips on exactly two errors: an alert judged below every accepted severity
# (missed) and a wake=no where the label says a person must act (false_quiet —
# the pipe drops cards on that answer). SKIP_EVAL=1 is the recorded way past a
# red gate in an emergency; a missing dataset skips with a notice, never fakes
# a pass. Cost per run: under a cent.
if [ "${SKIP_EVAL:-}" = "1" ]; then
  echo "eval gate SKIPPED by SKIP_EVAL=1 — the prompt ships unreplayed"
elif [ -f hookjudge/eval/dataset.jsonl ]; then
  docker compose -p hookstack-shadow --env-file .env -f deploy/docker-compose.shadow.yml     run --rm --no-deps     -v "$ROOT/hookjudge/scripts:/eval-scripts:ro"     -v "$ROOT/hookjudge/eval:/eval-data:ro"     -e PYTHONPATH=/app     hookjudge python3 /eval-scripts/eval.py --dataset /eval-data/dataset.jsonl --route ai --gate --votes 3
else
  echo "eval gate skipped: no hookjudge/eval/dataset.jsonl on this host (see hookjudge/eval/README.md)"
fi

docker compose -p hookstack-shadow --env-file .env -f deploy/docker-compose.shadow.yml up -d
docker compose -p hookprobe-prod  --env-file .env -f hookprobe/deploy/docker-compose.prod.yml up -d --build

# Read it back: a deploy is not done when compose returns, it is done when the
# things it started say they are healthy. lark-bridge has no healthcheck, so
# "running" is the most it can promise.
deadline=$(( $(date +%s) + 90 ))
# Exact names, anchored. `grep 'hook'` also matched a NEIGHBOUR project's
# containers (webhookwise-* contains "hook"), and the first read-back after
# that declared this deploy unhealthy over a one-off run container that was
# never ours to wait for.
# Derived from compose, not typed here. The typed list silently stopped
# covering hookjudge-c the day it was added: the readback said "deployed and
# healthy" while never looking at one of the four services it had just
# started. A list that has to be edited in lockstep with the compose file is
# a list that will be forgotten again.
compose_names() {
  docker compose -p hookstack-shadow --env-file .env -f deploy/docker-compose.shadow.yml ps --format '{{.Name}}' 2>/dev/null
  docker compose -p hookprobe-prod  --env-file .env -f hookprobe/deploy/docker-compose.prod.yml ps --format '{{.Name}}' 2>/dev/null
}
OURS="^($(compose_names | sort -u | paste -sd'|' -))$"
if [ "$OURS" = '^()$' ]; then
  echo "could not derive the container list from compose; refusing to read back nothing" >&2
  exit 1
fi
while :; do
  unhealthy=$(docker ps --filter "health=unhealthy" --format '{{.Names}}' | grep -E "$OURS" || true)
  starting=$(docker ps --filter "health=starting" --format '{{.Names}}' | grep -E "$OURS" || true)
  [ -z "$unhealthy" ] && [ -z "$starting" ] && break
  if [ "$(date +%s)" -ge "$deadline" ]; then
    echo "NOT HEALTHY after 90s:" >&2
    docker ps --format '  {{.Names}} {{.Status}}' | grep -E "$OURS" >&2
    exit 1
  fi
  sleep 3
done
docker ps --format '  {{.Names}} {{.Status}}' | grep -E " $OURS| ${OURS#^}" || docker ps --format '  {{.Names}} {{.Status}}' | awk -v re="$OURS" '$1 ~ re'

# Rollback anchors: only now, past the health gate, is this image set known-good.
# Tag each with the commit it was built from so a later bad ship can be reverted
# by re-tagging instead of rebuilding an older commit (which the prune then
# removes). The tags pin the layers, so pruning leaves the kept ones alone;
# ROLLBACK_KEEP bounds the disk they hold (the images share layers until source
# changes, so the real cost is roughly one extra hookprobe image per kept sha).
sha="$(git rev-parse --short HEAD)"
keep="${ROLLBACK_KEEP:-3}"
# Derived too, and for a sharper reason than the readback: the typed list
# carried `hookjudge-b:shadow`, an image that has never existed — b and c both
# run `hookjudge:shadow` — so that anchor skipped itself every deploy and said
# nothing. A missing image is now reported rather than passed over.
compose_images() {
  docker compose -p hookstack-shadow --env-file .env -f deploy/docker-compose.shadow.yml config --images 2>/dev/null
  docker compose -p hookprobe-prod  --env-file .env -f hookprobe/deploy/docker-compose.prod.yml config --images 2>/dev/null
}
for ref in $(compose_images | sort -u); do
  name="${ref%%:*}"
  if ! docker image inspect "$ref" >/dev/null 2>&1; then
    echo "  no rollback anchor for $ref (image not present)" >&2
    continue
  fi
  docker tag "$ref" "$name:$name-rb-$sha"
  # Keep the newest $keep rollback tags for this image, by image creation time.
  docker images "$name" --format '{{.Tag}} {{.CreatedAt}}' \
    | awk '$1 ~ /-rb-/ {print}' | sort -rk2 | tail -n "+$((keep + 1))" \
    | while read -r tag _; do docker rmi "$name:$tag" >/dev/null 2>&1 || true; done
done
echo "deployed and healthy. rollback anchor: -rb-$sha (kept $keep)"
