---
title: ANP evaluated — its identity layer needs a public HTTPS origin per agent, and one part of it is worth taking anyway
status: proposed
date: 2026-09-09
scope: stack
---

## Decision

**Not adopted as an identity or transport layer.** One piece of it *is* worth
doing and needs none of it: shape `GET /v1/agent` so it can be read as an ANP
**Agent Description** document. That is a serialization change in one endpoint,
adds no dependency, and is what makes a hookstack node discoverable by an ANP
client on the day anything outside this host wants to call it.

Evaluated by installing and running the SDK, not by reading its README —
`pip install anp` gives **1.0.1** (the README's own table still says 0.9.3),
35 transitive dependencies, 57 MB.

## What ANP is, and why it looked relevant

`agent-network-protocol/anp` is the multi-language SDK; the protocol lives in a
separate repo. It answers questions this stack currently answers privately:

| hookstack today | ANP's answer |
| --- | --- |
| `GET /v1/agent` — an ad-hoc "what this node is" | **Agent Description** document, crawlable by anyone |
| one shared HMAC secret per door, distributed by `.env` | **DID WBA** identity + **RFC 9421** HTTP Message Signatures |
| `handoff.py` — two nodes agreeing on field names | **OpenRPC** interface documents + JSON-RPC |
| no discovery; peers are named in compose | **WNS** — resolvable handles |

The second row is the one that matters. Every cross-node trust decision here
rests on a symmetric secret from one `.env`, which is correct on one host and
cannot work across organisations — and the bug fixed earlier today (two
adapters inheriting all thirteen service secrets) is a symptom of exactly that
model.

## What the SDK actually does, measured

**Key binding is self-certifying.** An `e1` DID embeds an RFC 7638 Ed25519
thumbprint in the DID string itself:
`did:wba:<host>:agent:planner:e1_<thumbprint>`. `verify_did_key_binding()`
returns `True` with **no network at all**. The generated document also carries
a `service` entry of type `AgentDescription` whose `serviceEndpoint` is a URL —
the slot `/v1/agent` would occupy.

**Body integrity is available but opt-in.** `get_auth_header(url)` alone signs
`("@method" "@target-uri" "@authority")` — no body. Pass `method=` and `body=`
and the covered set becomes `(… "content-digest")` with a `Content-Digest`
header added. So a caller who forgets the body argument silently loses payload
binding, which is the shape of defect the guards in this repository exist for.

**And the blocker: verification requires resolving the DID document over HTTPS
from the DID's own hostname.** `DidWbaVerifier.verify_request()` against a host
that does not resolve fails in 0.07 s with `Failed to resolve DID document` —
for an honest body and a tampered one alike. It never reaches the signature.
`DidWbaVerifier` calls `resolve_did_wba_document(did)` **hard-coded**, and
`DidWbaVerifierConfig` has no resolver, no base-URL override and no
trusted-document map. There is no supported way to verify against a local
store; monkey-patching the module function is the only route.

## Why that decides it

Every node here is bound to loopback behind a persisted host firewall, and
`HOOKRELAY_PUBLIC_URL` is loopback on both deployments — the reason the status
write-back note refused to write an audit link into a ticket. Adopting DID WBA
means giving **every agent** a public DNS name, a TLS certificate and a served
DID document. That is infrastructure this stack deliberately does not have, and
the requirement is symmetric: whoever wants to be authenticated must be
resolvable, so it binds hookstack as a client too, not only as a server.

**It also solves nothing this stack is short of.** ANP is a connection layer
with no opinion on whether an agent *should* do a thing. The tool gate, the
budget breaker, approvals, the delivery ledger, the WorkItem states and the
runtime contract are all outside its scope, and they are where this
repository's weight is.

## Consequences

**The Agent Description shape is the recommendation.** ANP's parser reads
`name`, `description`, `interfaces` / `openrpc`, `servers`, `url`, `version`,
`protocol`, `type`. `/v1/agent` already answers name, role, version, runtime,
workspace, policy and health. An AD-shaped projection of it is JSON, not a
dependency — and it keeps the option open at almost no cost.

**If ANP is ever adopted, it is an edge adapter and not an internal bus.** The
doctrine that produced the runtime contract applies unchanged: no hard
dependency on one SDK. 35 dependencies and a FastAPI-requiring server half
(`anp.openanp` will not even import without the `[api]` extra) do not belong
inside a service whose sibling holds a 5,500-line ceiling.

**The trigger to revisit is the same one the status write-back is waiting on:**
the day a node needs a reachable public address. Until then the two decisions
are blocked on the same missing fact, and should be reconsidered together.

**Maturity is not the concern; velocity is.** 1,423 stars on the spec repo,
Apache-2.0, six languages, shared cross-language conformance vectors, and a
README candid about per-language gaps — all good signs. But its published
version table is already behind its own PyPI release, which is what a fast
1.0 looks like. Pinning would be mandatory.
