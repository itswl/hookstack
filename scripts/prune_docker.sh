#!/usr/bin/env bash
# Weekly sweep of what rebuilds leave behind. Dangling images and build cache
# only — the two things `up -d --build` accumulates and nothing ever reads
# again. One day of deploys left 26 dangling images (4.7GB reclaimable) on a
# 48G disk that was already 63% full.
#
# DELIBERATELY NEVER: `docker volume prune` (an unattached volume can be a
# data volume between compose recreations — pruning it is deleting data),
# `docker container prune` (a stopped container can be evidence), or
# `image prune -a` (it removes every image no container currently uses,
# which during a partial outage is the image you are about to restart with).
#
# Install (host crontab):
#   45 4 * * 1 /srv/hookstack/scripts/prune_docker.sh >> /opt/backups/hookstack/prune.log 2>&1
set -euo pipefail

# until=24h so a layer created seconds ago by a deploy running RIGHT NOW is
# never pulled out from under it. Everything older and dangling is garbage.
before=$(docker system df --format '{{.Size}}' 2>/dev/null | head -1 || echo "?")
docker image prune -f --filter "until=24h" >/dev/null
docker builder prune -f --filter "until=24h" >/dev/null
after=$(docker system df --format '{{.Size}}' 2>/dev/null | head -1 || echo "?")
echo "$(date +'%F %T') pruned dangling images + build cache older than 24h: images $before -> $after"
