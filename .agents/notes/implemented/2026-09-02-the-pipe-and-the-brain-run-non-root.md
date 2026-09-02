---
title: The pipe and the brain run non-root with no capabilities
status: implemented
date: 2026-09-02
scope: stack
---

## Decision

hookrelay and hookjudge now run as uid 10001 (matching hookprobe), and the
shadow deployment drops all Linux capabilities and forbids privilege escalation
on all three:

- `hookrelay/Dockerfile` and `hookjudge/Dockerfile` add
  `useradd --uid 10001 app` + `mkdir /data && chown app /data` + `USER app`.
  uid 10001 is hookprobe's, so ONE chown convention covers the family.
- `deploy/docker-compose.shadow.yml` adds `cap_drop: [ALL]` and
  `security_opt: [no-new-privileges:true]` to hookrelay, hookjudge, hookjudge-b.
- The shadow-data bind mounts must be owned by 10001 (bind mounts keep host
  ownership); the compose says so and the deploy chowned
  `shadow-data/{hookrelay,hookjudge,hookjudge-b}`.

Shipped alongside as supply-chain hygiene the same review flagged: dependabot
gained a `docker` ecosystem entry per Dockerfile (base images were the one thing
nothing nagged). The matching CI change — the four workflows installing through
`requirements.lock` so CI cannot pass on versions the image never ships — is
made but NOT in this change: editing `.github/workflows/*` needs a
`workflow`-scoped push token this session's credential lacks. Land it with
`gh auth refresh -h github.com -s workflow`, add `-c requirements.lock` to the
`pip install -r requirements.txt` line in ci.yml / ci-hookjudge.yml /
ci-hookprobe.yml / release.yml, and push.

lark-bridge stays root for now (node:22-alpine, a different user model, and it
is the delivery path) — left in the proposed hardening note.

## Why

The review found only hookprobe was hardened; the pipe and the brain ran as root
with full default capabilities. They are plain HTTP servers that read config and
write a SQLite ledger — they need no privilege and no capability, and dropping
both bounds what a bug or a bad dependency can reach. The named-volume shape
(demo, quickstart, per-service deploy) inherits the image's `/data` ownership
automatically; only a bind mount needs the host chown, which is why the two prod
composes are the only place it matters.

## Consequences

- A fresh named volume works with no chown (CI's stack-smoke exercises exactly
  this). An EXISTING root-owned named volume, or a bind mount, must be chowned to
  10001 once — done for this host's shadow-data; noted in
  `hookrelay/deploy/docker-compose.prod.yml`'s bind for anyone using it.
- The eval-gate `run` container inherits the service's `/data` mount and runs as
  10001; it reads the world-readable dataset mount and writes nothing it cannot.
- If a container cannot open its db after this, the cause is ownership: the bind
  dir is not 10001. `chown -R 10001:10001` fixes it; there is no in-image
  workaround because a bind mount overrides image ownership.
- Verified after deploy: all three healthy, zero restarts, ledgers writing.
