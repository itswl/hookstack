---
title: The agent's bearer reads only, and the route that claimed this was already wrong
status: implemented
date: 2026-09-10
scope: hookprobe
supersedes: 2026-09-02-the-agent-shares-the-services-secrets
---

## Decision

`HOOKPROBE_TOKEN` is blanked out of the agent's subprocess. The agent gets
`HOOKPROBE_AGENT_TOKEN` instead — generated per process when unset — and
`require_token` refuses it on every method but `GET`.

The rule is the **method**, not a route allowlist. A list is a thing to forget
to add to: every write route added after this would have defaulted to reachable,
which is the wrong direction for a door whose whole point is what it refuses.

Two patrol briefs changed with it, because both were written against the token
that no longer exists:

* `run-rulings.md` now names `$HOOKPROBE_AGENT_TOKEN` for its reads.
* `automation-sampling.md` no longer POSTs regrets. It names them
  (`REGRET-CANDIDATE: <class>/<id> — why`) for a person to file.

## Why

This closes candidate (1) of
`proposed/2026-09-02-the-agent-shares-the-services-secrets.md`, which recorded
the residual after the env scrub: the agent still held the console's bearer, so
an injected instruction reaching a Bash step could `PUT /v1/memory` (no shape
check, unlike the suggestion-apply path), `PUT /v1/skills`, rewrite the system
prompt, approve a remediation, or `POST /hooks/agent` and spend money.

**Two things in the repository already disagreed about this, and the code sided
with the wrong one.** The regret route's own docstring says:

> Deliberately write-gated to the operator token, not reachable by any run: a
> regret is a label, and a label the automation could write about itself is not
> a label. **The agent's subprocess holds no token.**

That last sentence was false for its whole life. And
`automation-sampling.md` instructed the agent to POST a regret with exactly the
token the route said it did not have. A security decision written down with its
reasoning, against an operational instruction written against a token that
decision says should not exist — the boundary wins and the brief gets fixed.
Nothing real was lost: the automation ledger on the work deployment has filed
**zero** regrets, ever.

The engine's own comment was stale in the same direction. It read "HOOKPROBE_TOKEN
is deliberately NOT blanked: the run-rulings patrol has the agent POST to this
service's OWN API with it" — but that patrol was redesigned to propose-and-lift,
and its brief says outright *"you do not write these yourself and you hold no
token for it"*. What it actually needs is to **read** `/v1/runs`. So the residual
had been narrower than its own note for some time, and nobody had gone back to
check.

Generated rather than configured, when unset: nothing to put in a `.env`, nothing
on disk, and it rotates on restart. Set it only if something outside the
container needs the read-only surface.

## Consequences

* Twenty-third containment boundary.
* **What it does not remove**: everything a `GET` exposes, which is most of this
  service's own state, and anything the agent can do without this API — which is
  the whole shell. This takes away a write surface, not the run. The other
  candidate in the superseded note (restoring protected inputs from the pre-run
  snapshot rather than only flagging the change) is still open, and so is
  `PUT /v1/memory` wanting the shape check the suggestion path has.
* A deployment that hands the console token to something inside the container by
  other means (a mounted `.env` read by a tool, say) is unaffected by this; the
  blanking covers the agent's subprocess environment.
* If a later patrol genuinely needs to write, the answer is a marker line the
  service lifts — the pattern `run_rulings` already uses — not a route added to
  an allowlist.
