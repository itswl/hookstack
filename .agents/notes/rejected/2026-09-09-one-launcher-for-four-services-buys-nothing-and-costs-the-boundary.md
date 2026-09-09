---
title: A single-binary supervisor for the four services — rejected; it buys nothing that is missing and costs the boundary
status: rejected
date: 2026-09-09
scope: stack
---

## Decision

Not building an Erlang-style supervisor / single-executable launcher that starts
hookrelay, hookjudge, hookprobe and the bridge as child processes under one
file-locked parent, passing an encrypted authority token between them over local
unix sockets.

## Why

The proposal's premise was deployment burden: *"如果要在客户本地、开发测试机、或者
边缘物理节点快速部署，运维成本较高"*. That premise has been out of date since
`docker-compose.quickstart.yml` shipped. Today it is two commands, one file, no
checkout and no build:

```
curl -fsSLO .../docker-compose.quickstart.yml
docker compose -f docker-compose.quickstart.yml up -d
```

Images pinned at 0.3.0, a canned stub model inside the judge's image, a readable
sink inside the pipe's — no key, no bill. Six `restart: unless-stopped` policies
and health checks. **Compose is already the supervisor**, and it is one the
operator already knows how to read.

The cost is the part that decides it. The four services are separate for a reason
a shared parent process destroys: **hookprobe is the only one that runs an agent
and the only one that holds provider credentials**, and the twenty-two boundaries
in `docs/containment.md` are built on process and credential separation —
`SECRETS_WITHHELD_FROM_AGENT` blanking the family's HMAC keys out of the agent's
subprocess, read-only mounts, per-service secrets, a posture measured against real
credentials at startup. A launcher that hands those services an "authority token"
over a local socket replaces a boundary with a bearer.

The deliverable here is not a binary, it is a posture. Docker is doing real work:
it is what makes those claims true rather than asserted.

Worth naming what the proposal is right about, in case someone returns to this:
"one artifact, open the box and it runs" is a genuine virtue, and the quickstart
is this repository's answer to it. If that answer ever stops being enough, the
next move is a better quickstart — not a parent process that owns four
credentials.

## Consequences

* No launcher, no unix-socket authority token, no file lock.
* If an edge deployment ever genuinely cannot run a container runtime, this
  decision should be reopened **with that constraint stated**, because it is a
  different question from the one asked here.
* The other three items in the same review were taken and are recorded in
  `implemented/2026-09-09-a-proposal-remembers-the-world-it-was-written-in.md`,
  `implemented/2026-09-09-a-trace-id-derived-is-worth-more-than-one-propagated.md`
  and `implemented/2026-09-09-the-bridge-recycles-a-connection-it-does-not-hold.md`.
