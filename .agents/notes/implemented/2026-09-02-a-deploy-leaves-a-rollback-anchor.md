---
title: A deploy tags the image set it shipped, so rollback is a re-tag not a rebuild
status: implemented
date: 2026-09-02
scope: stack
---

## Decision

After the health gate passes, `scripts/deploy.sh` tags every image it just
shipped `<name>:<name>-rb-<short-sha>` and keeps the last `ROLLBACK_KEEP=3`.
Rolling back is then a re-tag + `up`, in seconds:

```
docker tag hookrelay:hookrelay-rb-<oldsha> hookrelay:shadow
docker compose -p hookstack-shadow --env-file .env -f deploy/docker-compose.shadow.yml up -d
```

The procedure is written into the deploy.sh header.

## Why

The images ship under mutable tags (`hookrelay:shadow`, `hookprobe:latest`), so
the previous image was untagged the moment a new one built and then removed by
the weekly prune — a rollback meant checking out the old commit and rebuilding,
which on this host is the ~10-minute build + the golden eval gate, during an
incident. A tag that pins the last-known-good layers turns that into a re-tag.

The tag is applied only PAST the health read-back, so an image set that failed
to come up never becomes a rollback target. `ROLLBACK_KEEP` bounds the disk:
the tags share layers with the live image until source changes, so the standing
cost is roughly one extra hookprobe image per kept sha, which the prune leaves
alone because a tagged image is not dangling.

## Consequences

- Fast rollback exists; the still-missing half is a version stamp the board can
  show (which running image is live). That needs a build-arg → env → /healthz
  path and stays in the proposed hardening note.
- The kept `-rb-` images are a small, bounded disk cost on a host already pruned
  weekly. If disk pressure appears, lower `ROLLBACK_KEEP`.
- Nothing changes for a normal deploy; the tagging is additive at the end.
