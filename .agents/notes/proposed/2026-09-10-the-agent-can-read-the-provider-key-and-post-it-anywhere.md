---
title: The agent can read the provider key and post it anywhere — the proxy is built, the topology is the decision
status: proposed
date: 2026-09-10
scope: stack
---

## Decision

Not deployed. `deploy/egress-proxy/proxy.py` exists and is tested: a stdlib-only
forward proxy on a hostname allowlist, CONNECT and absolute-URI HTTP, refusing
and **logging** everything else. What it needs is a network topology that puts a
probe behind it, and that is a decision about deployments this repository does
not wholly own.

## Why

Two facts, measured on 2026-09-10 rather than reasoned about:

```
ANTHROPIC_AUTH_TOKEN   in container=True   blanked from the agent=False
docker exec hookprobe curl https://example.com   ->   200
```

Fourteen secrets are blanked out of the agent's subprocess. The provider key is
not, and cannot be: the CLI needs it, and the agent's Bash is a child of the CLI
and inherits its environment. Hiding it would take the CLI scrubbing its own
children, which is not ours.

So the honest sentence is: **a Bash step, or an injected instruction that
reaches one, can read the provider key and POST it to any address on the
internet.** The container boundary is about somebody breaking IN. This is the
other direction and nothing bounded it.

Worse on the work deployment, where it is not only the model key:

```
work-probe-work   bash_guard=danger-only   chat_senders=["*"]
  /data/aws/credentials  READABLE as uid hookprobe(10001) — 119 bytes, 1 access key
```

**Why a proxy and not a firewall rule.** Names, not addresses: the gateway and
AWS are names whose IPs move. And the refusal is the point — a firewall drop is
a silence, where this logs `REFUSED <host> — not on the egress allowlist` at
WARNING, which is the only place that can say a run tried to reach somewhere
nobody listed.

**Why it is not deployed, which is the whole content of this note.** A container
cannot be on a docker network without also getting that network's route out. The
production investigator must stay reachable INBOUND at `hookprobe:8088` — from
the pipe, and until 2026-09-14 from the platform's own deep-analysis leg,
since switched off — so it must stay on
`hookstack_net`, and being on `hookstack_net` is exactly what gives it the
egress this would remove. Two ways through, both needing a decision:

1. **Everything that talks to the probe joins a new `internal: true` network.**
   Clean, and it makes the proxy the only route out. `hookrelay` is ours;
   `webhook-receiver` (the probe's MCP server) and the Alloy collector belong to
   the WebhookWise deployment, so this is a coordinated change across two
   compose files, one of which is not in this repository.
2. **Host firewall rules keyed to the probe's container IP** (the host already
   persists nft + systemd). No topology change and no proxy in the data path,
   but it needs a static IP on a network this repo does not own, it is
   address-based against names that move, and a drop tells nobody anything.

The local work deployment is the HARDER case, not the easier one, which is worth
recording because the instinct is the opposite: its MCP server is plain HTTP at
`host.docker.internal:52222`, which an internal network cannot reach at all.

**The partial that already exists**, so the next reader does not think there is
nothing. The output guard's pattern list carries
`("openai-style api key", r"\bsk-[A-Za-z0-9_-]{20,}\b")`, and the provider
credential is `sk-` + 48 — so it matches. An agent that reads it INTO tool
output (`env`, a printenv, a config dump) gets `output_secret` recorded on the
run and shown above the transcript. Two honest limits: it is detection, not
prevention, and the token never appears in output when the agent uses it
directly — `curl -H "Authorization: Bearer $ANTHROPIC_AUTH_TOKEN"` is expanded
by the shell, so the guard sees a variable name in the command and no secret in
the answer. It converts the most likely accidental path, and the most likely
path an injected instruction takes, from silent into recorded. It does not close
the hole.

**The policy half shipped** on the same day, as
`implemented/2026-09-10-egress-by-policy-not-by-topology.md`: the probes keep
their network and route outbound through the proxy by `HTTP_PROXY`/`HTTPS_PROXY`.
That measurement also killed option 2 outright — the gateway is Cloudflare-fronted,
so an IP allowlist for it admits everything else behind Cloudflare. What remains
open here is option 1 and only option 1: enforcement, which needs the probe off
the shared network, which needs the neighbouring compose.

## Consequences

* The proxy is on the stack gate (`compileall`, ruff, its own tests) from the
  day it landed rather than after it breaks — the lesson `lark-bridge` taught by
  being checked by nothing while live on the callback path.
* **Until one of the two is chosen, the residual stands and is now written
  down.** That is the point of a proposed note: the next person is deciding
  rather than discovering.
* The allowlist tests are about the ways it could be wrong while looking right —
  a suffix rule admitting `notexample.com`, a stray comma creating a blank rule
  that matches the empty host, an unset variable read as "allow everything".
  Empty means refuse everything, deliberately: silent-open is the failure that
  would never be noticed.
* What it would not stop, stated so nobody oversells it: an allowlisted name
  that accepts attacker-chosen bodies is still a way out — the model gateway
  takes POSTs. It bounds the accident and the opportunist, and it makes the
  attempt visible.
