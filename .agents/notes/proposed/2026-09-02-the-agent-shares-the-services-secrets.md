---
title: The agent still shares a uid and a bearer with the service
status: proposed
date: 2026-09-02
scope: hookprobe
---

## Decision

Not built. Record the residual after
[[the-agent-subprocess-does-not-inherit-the-services-secrets]] closed the worst
of it, so the next person is deciding rather than discovering.

The agent still runs in the same container and uid as the service, and
`HOOKPROBE_TOKEN` is still in its environment (the `run-rulings` patrol needs
it). So an injected instruction that reaches a Bash step can still:

- `curl -H "Authorization: Bearer $HOOKPROBE_TOKEN" -X PUT .../v1/memory` and
  rewrite CLAUDE.md wholesale — the direct PUT (`library.py`) has no shape check,
  unlike the suggestion-apply path (`suggestions.py`). Same for `PUT /v1/skills`,
  `/v1/system-prompt`, `POST /v1/remediations/{id}/approve`, and spawning paid
  runs via `POST /hooks/agent`.
- `echo '- …' >> /data/CLAUDE.md` — the input guard only DETECTS a fingerprint
  change (`engine.py`, `service.py`); nothing reverts it, so the injected line
  steers the next run.

Two candidate fixes, neither adopted yet:

1. A lesser credential for the agent's own self-calls (rulings only), distinct
   from the admin bearer the console uses — so the token in the agent env cannot
   reach memory/skills/remediation writes.
2. Restore protected inputs from the pre-run snapshot when the fingerprint
   changes, rather than only flagging it, so a bash `>>` to CLAUDE.md/skills is
   undone before the next run loads it.

## Why

The clean boundary (a separate uid, or a broker that holds every secret and the
agent holds none) is a real architecture change, and `run-rulings` actively
depends on the agent holding the bearer. Shipping the env-scrub first removes
cross-service forgery and the platform/admin/Lark exposure — the parts with the
widest blast radius — without breaking the weekly patrols. What remains is
scoped to this service's own surface, which already has other gates
(remediation allowlist, the memory suggestion shape check on the non-direct
path), and is worth doing deliberately.

## Consequences

- Until (1) or (2) lands, treat the agent as able to write this service's own
  memory/skills if steered, and keep relying on the live red-team
  (`scripts/redteam_memory.py`) to catch the auto-apply path. H2 in the review —
  the memory auto-apply shape check stops phrasing, not meaning — is part of the
  same surface and belongs with this note's fix.
- The direct `PUT /v1/memory` wanting the same shape check the suggestion path
  has is the smallest independent step and could ship on its own.
