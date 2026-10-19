---
title: The allowlist names shapes, and a name must resolve where its kind says
status: implemented
date: 2026-10-19
scope: stack
---

## Decision

Three changes to what leaves a probe and what a probe keeps, each one a gap a
second deployment running copies of these files found in use and closed:

1. **`EGRESS_ALLOW` admits two named shapes, `@aws-api` and `@aws-s3`, and the
   work compose's default uses them instead of `.amazonaws.com`.** `@aws-api`
   is `<service>[.<region>].amazonaws.com`, any service, any region; `@aws-s3`
   is a bucket in every spelling. Neither matches an EKS cluster endpoint, an
   EC2 instance's public name, a load balancer or an API Gateway stage: the
   first label of those is anyone's. A cluster the operator means is one bare
   name in the operator's own list. An unknown `@` rule stops the container at
   start.
2. **A listed name is vetted at the address.** The proxy resolves the name,
   refuses any answer that is loopback, link-local, private, reserved or the
   address a desktop container runtime maps the host machine to, and connects to
   the address it vetted — never a second lookup. Compose peers are named in
   `EGRESS_INTERNAL` and held to the inverse: they must resolve inside the
   compose network. The name is checked before any lookup. A name that does not
   resolve is a 502, not a 403.
3. **A chat bot's incoming-webhook URL is a credential.** The investigator's
   output guard names the kind (`chat webhook url`) and the redactor masks the
   token half of Feishu/Lark, Slack, DingTalk, WeCom and Discord hooks while
   keeping the host.
4. **Scratch and the CLI's own logs have a retention window of their own**
   (`HOOKPROBE_SCRATCH_DAYS`, default 7): the agent subprocess gets
   `TMPDIR={workdir}/scratch`, and that directory plus `$HOME/.cache/claude-cli-nodejs`
   are pruned daily, whatever `HOOKPROBE_RETENTION_DAYS` says.

## Why

- `.amazonaws.com` was written for the planner's service calls and admitted
  every hostname anyone can obtain under that suffix. The sibling verified it
  with the matcher itself; the operator's list carried the same rule.
- The list was on names and the connection on addresses, with nothing between
  them. In this family's topology the probes have no route, so the risk sat in
  the doors container, which does — and that container is the one with the
  allowlist in it.
- On 2026-10-12 the sibling's action job read a function's environment and a
  chat webhook URL went into the transcript, two logs and the model call.
  Nothing in `redact.py` or `_SECRET_SHAPES` named the shape, and this family
  posts its own cards through exactly such URLs.
- The sibling found a day-old scratch holding other teams' configs with on-call
  numbers. Here the measured accumulation was different and older: the CLI's
  MCP transport logs under `home/.cache` had grown since 2026-09-04 on two
  nodes, carrying whatever tools returned, with nothing reading them and
  `retention.prune` not naming them. Case files stay kept-forever by default
  because they are recall; these never were.

## Consequences

- Operators whose `.env` pins `EGRESS_ALLOW` keep what they pinned until they
  change it; the work stack's line now carries the two shapes and its cluster
  endpoints by name.
- A compose peer reached through the proxy has to be in `EGRESS_INTERNAL`; the
  two deploy composes list theirs. A peer name that resolves to the host is
  refused, which is the point.
- The redaction keeps the platform host, so a case file still says which chat
  was posted to.
- `HOOKPROBE_SCRATCH_DAYS=0` keeps scratch and the logs; the window is per node
  like every other knob of its kind.
