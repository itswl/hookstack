---
title: A deploy continues the run — a graceful stop leaves a turn the way a crash does
status: implemented
date: 2026-10-08
scope: hookprobe
---

## Decision

When the service stops gracefully, a turn in flight that can be continued is
left unfinished on disk, with its engine session id, for the next boot's
`recover_orphans`. That is exactly how a crash leaves it. A turn that cannot be
continued settles as a failure that reports itself, as before: no session yet,
its one resume spent, the budget out, or `HOOKPROBE_RESUME_INTERRUPTED=off`.

`scripts/work_drain.sh` waits for the work stack's runs in flight before a
recreate, up to fifteen minutes by default, and names what is still running if
the wait runs out.

## Why

The operating guide said a run in flight at "a crash, an OOM kill, a redeploy"
is continued at startup. For a redeploy it was not. `shutdown()` cancelled the
turn, `_execute` settled it as failed "cancelled during shutdown", and the boot's
sweep skips a finished run. Three runs were lost that way in one week of
2026-09 on the work stack, one of them the only real piece of work on the board.

The shutdown docstring defended the old choice: settling at once "beats the next
boot's sweep". That was written before the sweep learned to continue a run. Once
it could, settling at shutdown threw away the one path that keeps what the
interrupted attempt gathered.

The drain exists because continuing is not free. A resume is another turn, and
the interrupted turn's spend is recorded as unpriced. A run near its end costs
nothing if the recreate waits for it.

## Consequences

- A stopped node that is not started again holds its runs as running until it
  is. The pipe waits on the report meanwhile, as it would for a crash.
- The bounds on the resume path apply unchanged: one resume per run, the budget
  breaker, and the off switch. A deploy loop cannot become a spend loop.
- A run started after the drain's last look rides the recreate and is continued
  at the next boot. The drain is a saving, not a guarantee.
- The drain reads each probe through the doors container with the token from the
  probe's own environment. A board that does not answer counts as busy.

## Rejected

- **Only the drain.** It covers the deploys somebody remembers to drain, not a
  Docker Desktop restart, an update or a reboot, which stop the stack the same
  graceful way.
- **Waiting for the turn inside `shutdown()`.** An investigation can run thirty
  minutes, and the container's stop timeout would kill it anyway.
