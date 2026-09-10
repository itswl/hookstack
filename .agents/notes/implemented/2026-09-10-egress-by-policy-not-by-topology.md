---
title: Egress by policy, not by topology — the refusal log without the coordination
status: implemented
date: 2026-09-10
scope: stack
---

## Decision

Both deployments now route the investigators' OUTBOUND calls through
`deploy/egress-proxy/proxy.py` on a hostname allowlist. The probes keep their
network: `HTTP_PROXY`/`HTTPS_PROXY` point at the proxy, `NO_PROXY` carries
everything reachable locally direct.

This is the third option, and neither of the two the earlier note posed.

## Why

The two options in
`proposed/2026-09-10-the-agent-can-read-the-provider-key-and-post-it-anywhere.md`
were a topology change needing a neighbouring deployment's compose, or host
firewall rules. Measuring both killed one and stalled the other.

**Host firewall rules are out, and this is the finding rather than a
preference.** The one external destination production needs resolves to
Cloudflare:

```
model-gateway.example -> 2606:4700:3035::ac43:8164, 2606:4700:3037::6815:2a4
```

An IP allowlist for a Cloudflare-fronted host admits everything else behind
Cloudflare. The rule would read "only the model gateway" and mean "any of a
large fraction of the internet". Not brittle — close to useless. And a drop is
silent, where the refusal is the entire product.

**The topology change is real work, not aspiration.** `hook:deep-analysis`
opened 121 of ~200 production runs — the dominant caller — and it reaches the
probe by DNS name on the shared network. Moving the probe off that network
means changing a compose this repository does not contain.

So: policy. The probes keep their route, and everything that HONOURS a proxy
goes through one allowlist. What that is and is not:

* It bounds the accident and the injected instruction that uses ordinary
  tooling — which is the threat that actually exists here. It does not stop a
  process that deliberately ignores the proxy environment.
* The allowlist was measured, not guessed. WebFetch reached exactly three hosts
  in this deployment's life (`docs.aws.amazon.com` 7, `grafana.com` 2,
  `raw.githubusercontent.com` 2); WebSearch needs nothing, because its audit
  details are QUERIES not URLs — the search runs server-side and rides the model
  API. So an empty refusal log now means "nothing new", which is a signal;
  under a gateway-only list it would have meant "WebFetch is broken".
* `NO_PROXY` keeps the proxy out of the path of everything local, including
  `127.0.0.1`, where the CLI posts its own telemetry. If the proxy dies, the
  return door, the MCP server and the collector keep working; only the model
  call stops.

**Proven before shipping, including the leg that could not be reasoned about.**
From inside a probe: gateway 200, `docs.aws.amazon.com` 200, `example.com` and
`api.github.com` both refused, in-network and loopback direct at 200. The proxy
log caught the posture check's own `sts.` and `iam.amazonaws.com` calls, which
is the AWS SDK honouring it unprompted. And a real turn was paid for rather than
assumed — $0.1856, `completed`, answer `OK`, 17 gateway connections through the
proxy — because shipping an unproven model path would have taken the
investigator dark.

## Consequences

* **The proxy is a new dependency for external calls.** Small, stdlib-only, on
  the stack gate, `restart: unless-stopped`, and out of the path of everything
  local — but it is one more thing that can be down, and when it is, runs fail
  at the model call.
* `EGRESS_ALLOW` is a deployment variable. A new destination is a one-line
  change and a refusal in the log is what tells you to make it.
* The work deployment's list is wider by necessity — `probe-work` is
  `danger-only` and holds an AWS credential its own uid can read — which is why
  the refusal log matters more there, not less.
* **The proposed note stands.** Enforcement still needs the topology, and the
  honest position is now: the policy half is in, the log will say whether the
  coordination is worth asking for, and that conversation can be had with
  evidence instead of a hypothesis.
