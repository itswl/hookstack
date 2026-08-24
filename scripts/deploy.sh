#!/usr/bin/env bash
# The whole deployment, one command, from the checkout it lives in:
#
#   ./scripts/deploy.sh              # pull main, rebuild both projects, read back health
#   DEPLOY_NO_PULL=1 ./scripts/deploy.sh   # rebuild what is already checked out
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
docker compose -p hookstack-shadow --env-file .env -f deploy/docker-compose.shadow.yml up -d --build
docker compose -p hookprobe-prod  --env-file .env -f hookprobe/deploy/docker-compose.prod.yml up -d --build

# Read it back: a deploy is not done when compose returns, it is done when the
# things it started say they are healthy. lark-bridge has no healthcheck, so
# "running" is the most it can promise.
deadline=$(( $(date +%s) + 90 ))
while :; do
  unhealthy=$(docker ps --filter "health=unhealthy" --format '{{.Names}}' | grep -E 'hook|lark' || true)
  starting=$(docker ps --filter "health=starting" --format '{{.Names}}' | grep -E 'hook|lark' || true)
  [ -z "$unhealthy" ] && [ -z "$starting" ] && break
  if [ "$(date +%s)" -ge "$deadline" ]; then
    echo "NOT HEALTHY after 90s:" >&2
    docker ps --format '  {{.Names}} {{.Status}}' | grep -E 'hook|lark' >&2
    exit 1
  fi
  sleep 3
done
docker ps --format '  {{.Names}} {{.Status}}' | grep -E 'hook|lark'
echo "deployed and healthy."
