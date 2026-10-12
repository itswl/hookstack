---
title: A credential nobody can measure is not a posture
status: implemented
date: 2026-10-12
scope: stack
---

## Decision

A mounted credential whose measuring tool is not in the image gets its own
verdict, `unmeasurable`, and it is never a pass:

- a `readonly` node refuses to start under `HOOKPROBE_POSTURE_CHECK=enforce`
  and says which tool is missing;
- a writing node (`danger-only`) starts, records the verdict, and pages on
  `HOOKPROBE_ALARM_URL` at once;
- the hourly selftest's posture check fails until the tool is back;
- what WAS measured still outranks it: a violation found on one credential is
  `wider-than-declared` whether or not the other credential's tool exists.

And the shape that caused it is refused at the gate: services that share an
image tag must build it alike (`scripts/assert_shared_images_build_alike.py`,
in `scripts/gate.sh` and `ci-stack`). The work compose's `probe-watch` build now
passes `KUBECTL_VERSION` exactly as `probe-plan` and `probe-work` do.

## Why

The work stack's three probes share `hookprobe:work`. A compose builds every
service that has a `build:` block, and the tag holds whichever build ran last;
`probe-watch`'s build passed no kubectl version. From 2026-09-29 the image had
no kubectl. At every boot after that both credentialed nodes recorded
`can-i --list: kubectl: not found` — the planner's verdict `unverifiable`, the
work node's `recorded` with `mutating: []` — and both started. The hourly check
asks one question, "wider than declared?", and answered held every hour. The
stack ran this way until it was stopped on 2026-10-09; nothing paged, no gate
went red, and the record read like a cluster with no mutating grants.

`unverifiable` was built for an identity that may not ask about itself. A
missing binary is a different silence: the far side was never asked, and the
operator can fix it with a rebuild — which is exactly why it must be loud.

## Consequences

- A rebuild that drops a tool now stops a readonly node at boot instead of
  degrading its planner quietly; a writing node keeps running but says so.
- The posture record carries `tool_missing` on the credential it could not
  measure, and `GET /v1/posture` and the audit cite it.
- `assert_guards_are_tested.py` carries the mutation: turning `unmeasurable`
  back into `recorded` must fail `tests/test_posture.py`.
- The new gate check parses the composes by indentation and has a floor; a
  compose written in another shape, or one that stops building something,
  needs the floor lowered on purpose.
- Already-built images are not rebuilt by this change; the work image needs
  `docker compose ... build` before the next recreate.
