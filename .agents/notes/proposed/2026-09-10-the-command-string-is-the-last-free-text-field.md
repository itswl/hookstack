---
title: The command string is the last free-text field on the execution path
status: proposed
date: 2026-09-10
scope: hookprobe
---

## Decision

Replace the free-text `command` in the fenced `remediation` block with an
`action` + `params` pair, resolved against a registry an operator writes. The
model names an action and fills its parameters; a template in the registry
renders the argv; nothing the model wrote is ever the argv itself.

It is NOT a parallel typed action surface beside the existing one. The block is
already a schema — `action`, `command`, `target`, `risk`, `rollback` — with
exactly one field that is free text and one field that is executable, and they
are the same field. This narrows that field; it adds no new door.

Deliberately part of it:

* **The registry is deny-by-default and lives beside the allowlist**, read from
  a path an operator sets, hot-read per step like `HOOKPROBE_REMEDIATION_ALLOWLIST`.
  An action name that is not in it is refused with the name it was asked for.
* **Parameters are typed and bounded at render time** — a pattern per parameter,
  refused rather than escaped. `unit=api.service` renders; `unit=api; rm -rf /`
  does not match `[a-z0-9._-]+` and never reaches a template.
* **Free-text `command` keeps working, under the allowlist, unchanged.** Every
  proposal on disk today carries one, and an upgrade that made five parked rows
  unreadable would be the "absent is not stale" mistake in a new place.
* **The allowlist still applies to a rendered argv.** Two gates in series, not
  one replaced: the registry bounds the SHAPE a command may take, the allowlist
  bounds the INSTANCE that reaches a process. Either refusing is a refusal.

Deliberately not part of it:

* **Auto-rollback on a metric that did not improve.** The review that prompted
  this asks for "if the error rate has not dropped in 3 minutes, roll back". This
  node holds no credentials for the systems it writes procedures about and must
  not: a watchdog that opened one would be a second, unaudited way of touching
  them — the constraint the freshness cursor row already states. The honest
  shape is to read the pipe's NEXT signal (a recovery arriving, or the same
  condition firing again) and say what happened after the procedure ran. That is
  a separate note and a smaller one.
* **A second executor.** `execute()` already refuses anything needing a shell and
  runs an argv; a rendered template arrives at exactly the same place.

## Why

**Because the last review was right about the residual and wrong about the
rest.** Its five proposals were mostly already built (target cooldown, risk
gating, minimal executor environment — all shipped 2026-09-10), or impossible
here as drawn (an eBPF/gVisor/Kubernetes-Job story for a docker-compose host
with no Kubernetes), or contradicted by another of its own proposals: "capture
100% of stack dumps automatically at alert time" and "hold no standing
credentials" cannot both be true, and it did not notice. What survived is this
one: the model still writes the string that becomes an argv.

**The gates in front of it are real, and none of them is this.** The allowlist
bounds what may run; the click bounds who may run it; the cooldown bounds how
often; the risk gate bounds how dangerous. All four take the command as given.
An operator pattern written a little too wide is the whole residual, and the
containment row has said so in those words since the day it was written.

**And two of today's boundaries key on fields the model writes** — the
cooldown on `target`/`command`, the risk gate on `risk`. Each says so where it
lives, because a gate whose key its subject controls can only add a
requirement, never catch a lie. A registry is the one change in this family
that moves a decision from the model's text to the operator's file.

**The cost is a prompt change, and that is the honest reason this is a note.**
The report has to emit a different block, which changes model behaviour on
every investigation, which is the one kind of change this repository cannot
verify with a fixture. It wants its own pass with a golden replay, not a ride
along a bug fix.

## Consequences

* **Nothing becomes newly runnable.** The shipping default stays collect-only:
  no allowlist file and no registry file means proposals still pile up and
  nothing executes. This adds a second lock to a door that is welded shut on
  the only deployment there is.
* **The registry is a file somebody has to write, and empty is the safe
  state.** Same shape as the allowlist, same failure mode as the allowlist: a
  template written too wide is an operator's mistake and this only holds it.
  What it removes is the class where the model, not the operator, chose the
  shape of the string.
* **It will read as more safety than it is if the ceiling is not stated.** A
  registry cannot stop an operator registering `run-shell(cmd)`. Say that where
  the registry lives, in the same words the input guard uses.
* **The "a second structured block is a new surface" objection is now answered
  with evidence rather than argument.** `blockers.py` shipped one on
  2026-09-11 — a fenced ```blocked``` block the service lifts exactly the way it
  lifts ```remediation``` — against a measured before-state: 11 `task` reports
  on the planning node, 10 carrying an `unknowns` section, **0** carrying one
  runnable command or one name an operator could act on, and the same missing
  credential opening three consecutive reports. Whoever picks this residual up
  should cite those numbers and whatever ```blocked``` does on the deployment,
  rather than re-deriving the case for asking a model to fill a schema.
* **Backward compatibility is the part most likely to be got wrong.** Five rows
  on production carry a free-text `command` and were written before any of
  this; they must stay readable, refusable and approvable exactly as they are.
  See [[three-gaps-before-the-execution-door-opens]] for the three that landed
  before this one, and the containment table for what each does not stop.
