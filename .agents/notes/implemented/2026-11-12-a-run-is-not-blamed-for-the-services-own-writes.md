---
title: A run is not blamed for what the service itself wrote to its inputs
status: implemented
date: 2026-11-12
scope: hookprobe
---

## Decision

The before/after comparison of a run's input surface (`inputs.fingerprint`,
`inputs.changes`) subtracts the service's own writes made while the run was
out. Every service write to the surface — the memory writer behind both the
auto-applied and the accepted suggestions, the post-run runbook steps (auto
distill, consolidation), and every console write door under `/v1/memory`,
`/v1/skills`, `/v1/agents`, `/v1/system-prompt` (one middleware) — runs inside
`inputs.service_writes`, which records each file it changed as a step from one
digest to the next. The engine takes `inputs.service_mark()` before its
fingerprint, and at the end a change counts as the run's unless the service's
steps after that mark, followed in order from the digest the run started with,
end at the digest it left.

## Why

Found re-shooting the docs pictures on 2026-11-12: five runs on a demo stack,
two at a time. One finished, its report offered a plain fact, and the service
applied it to `CLAUDE.md` (`HOOKPROBE_MEMORY_AUTO_APPLY` is on by default). The
run still in flight ended with `run rewrote its own inputs: modified
/data/CLAUDE.md`: its record carried the tamper warning in the console, and its
runbook was skipped as "run changed its own inputs". The same follows from an
operator saving the memory page, or a finished run's runbook being installed,
while any run is out. Agent and service share a UID and a process tree, so
nothing about the file says who wrote it; only the service knows what it wrote.

The excuse is a chain of exact digests, not "the service touched this file":
- a run that edits the memory before the service appends is still caught, since
  the service's step starts from the run's version, never from the run's start;
- a run that edits after is caught, since its end state is no step's end;
- a service step before the run's mark excuses nothing, so an old step cannot be
  replayed by a run writing the same bytes again;
- a step that falls out of the kept window (the last 2000) excuses nothing either,
  which is the safe direction to be wrong in.

## Consequences

- `inputs.changes(before, after)` without `since` is unchanged; the engine passes
  the mark.
- A run's own write that lands inside one service write window would be
  recorded as the service's. The window is one write long, and the input guard
  refuses the tool call before it gets that far; the comparison is the second
  line, not the first.
- A new place in the service that writes the surface outside the doors must
  wrap the write in `inputs.service_writes`, or a run in flight is flagged for it
  again — the failure is a false warning and a skipped runbook, not a missed one.
- `scripts/assert_guards_are_tested.py` mutates the excuse into "any change is
  excused once a mark is given"; the tests in `tests/test_inputs.py` catch it.
