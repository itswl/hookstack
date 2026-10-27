---
title: What the guard cannot see, what the shell can still remove, and a failure that keeps happening
status: implemented
date: 2026-10-27
scope: hookprobe
---

## Decision

A second deployment running copies of these files added a deny list for its
unattended agent and a page for failures that repeat. Run against this repo's
own gate and failure path, three of its lessons held here too.

1. **A script the guard never sees is refused, under every posture.** A pipe
   into a shell (`curl … | sh`, `wget -qO- … | bash`, `| sudo bash`) and a
   fetched script handed to one (`bash -c "$(curl …)"`, `sh <(curl …)`,
   `source <(…)`, `eval "$(…)"`) were allowed in both postures: the guard read
   `sh`, and every verb in the script stayed out of reach of every rule.
2. **Removing or relinking a protected path from the shell is a write.**
   `rm results/x.json`, `rm -f CLAUDE.md`, `find results -delete` and
   `ln -sf /tmp/x .claude/skills/x` passed while `cp` and `mv` into the same
   paths were refused — the case files the next run reads could be deleted, and
   a skill planted whose content lives wherever the run can write.
3. **A transient failure that keeps happening pages.** Rate limits and 5xx are
   retried and, when they fail again, filed as one investigation's bad luck;
   none of them is node-wide, so none paged. The third consecutive failure of
   one kind now pages once per kind per six hours, and a run that lands says
   so. The other deployment lost three days of rounds to a rate limit that way.

Also: `aws logs start-query` (and CloudTrail Lake's) is a read in the readonly
posture, named exactly so Athena's `start-query-execution` stays refused.

## Why

Each was measured, not reasoned: the commands from the other deployment's list
went through this gate in both postures and the gaps were the ones above; the
failure path had no count of anything but node-wide shapes.

## Consequences

- An installer's `curl … | sh` is refused even on a writing node; fetch it to a
  file, read it, run what it holds. A script saved and then run is two steps and
  still passes — the guard's stated limit, not a new one.
- `ln -s <protected> /tmp/link` is refused too, which over-blocks a link that
  only points at a protected file; writing through such a link was already
  refused, and the guard errs toward over-blocking.
- A streak lives in memory: a restart starts the count again.
- Rollback: nothing persisted changes shape.
