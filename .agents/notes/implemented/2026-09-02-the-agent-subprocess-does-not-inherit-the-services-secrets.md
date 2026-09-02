---
title: The agent's CLI subprocess does not inherit the service's secrets
status: implemented
date: 2026-09-02
scope: hookprobe
---

## Decision

Two changes narrow what a Bash step (or an injected instruction that reaches
one) can read out of the environment:

- `hookprobe/deploy/docker-compose.prod.yml` no longer has `env_file: ../../.env`.
  It injected the WHOLE deployment `.env` into the container — the Lark app
  secret, `SHADOW_INGEST_SECRET` (which "can forge judgements"),
  `SHADOW_ADMIN_TOKEN`, `WW_RELAY_SECRET` — none of which this service reads.
  The `environment:` block already names everything the service needs, pulled
  from `.env` by compose interpolation. The one key that reached the container
  ONLY through `env_file`, `HOOKPROBE_BUDGET_GATES_AGENT_DOOR`, is now named
  explicitly.
- `engine.py` adds `_subprocess_env()`, which blanks the family HMAC keys
  (`HOOKPROBE_EVENT_SECRET` / `RETURN_SECRET` / `RULING_SECRET`) and, defensively,
  the sibling-service secrets in the same `.env` from the CLI subprocess env.
  The SDK inherits `os.environ`, so a variable is removed by overriding it to
  `""` in `options.env`.

`HOOKPROBE_TOKEN` and `ANTHROPIC_*` are deliberately NOT blanked — see the Why.

## Why

The review found the "agent proposes, the service holds the credential" boundary
was not enforced: the CLI subprocess inherited the service's whole environment,
so `$SHADOW_ADMIN_TOKEN`, `$WW_RELAY_SECRET`, the Lark secret and the family
signing keys were one `printenv` away, and the bash guard has no rule for
`env`/`printenv`. Verified on the box: `docker exec hookprobe env` listed all of
them. With the signing keys, an agent could forge signed reports, rulings and
escalations that the service exists to sign FOR it; with the ingest secret it
could forge judgements at the shadow judge.

Removing `env_file` is safe because the probe package never reads any
`LARK_*` / `SHADOW_*` / `WW_*` variable (grep-verified), and the `environment:`
block is a superset of what `settings.py` reads.

`HOOKPROBE_TOKEN` stays because the `run-rulings` patrol has the agent POST to
this service's OWN API with it (`examples/patrols/run-rulings.md`), and that
patrol runs weekly in production. `ANTHROPIC_*` stays because the model call
needs it.

## Consequences

- The agent can no longer read the platform/admin/Lark secrets or the family
  HMAC keys, so cross-service forgery via a Bash step is closed.
- The residual is `HOOKPROBE_TOKEN` in the agent env: an injected instruction
  could still make the agent call this service's own write surface
  (`PUT /v1/memory`, `PUT /v1/skills`, remediation approve). That is tracked in
  [[the-agent-shares-the-services-secrets]] — the real fix is a lesser-scoped
  credential or a separate uid, which is a larger change.
- Run the live memory red-team after any change to this path:
  `hookprobe/scripts/redteam_memory.py` (AGENTS.md, "Verify at the point of
  consumption"). It needs the provider key and the real image on the deploy host.
- If a future `.env` adds another secret the agent should not see, add it to
  `_SECRETS_WITHHELD_FROM_AGENT`.
