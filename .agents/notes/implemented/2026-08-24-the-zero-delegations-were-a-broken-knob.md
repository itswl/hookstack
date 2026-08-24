---
title: The zero delegations were a broken knob, not a preference
status: implemented
date: 2026-08-24
scope: hookprobe
---

## Decision

The delete-the-roles verdict scheduled for 2026-08-28 is void, and the
observation window restarts today with two corrections — one to the system
under test, one to the test itself.

## Why

c599e08 installed delegation guidance and set a rule: let the audit log
decide; a week of zero `Task` entries deletes the three roles. By 2026-08-24
the count stood at 0 in 163+ tool calls, on track for deletion.

Both halves of that measurement were broken:

1. **`CLAUDE_CODE_SUBAGENT_MODEL` held a bare provider model id** where the
   CLI expects a tier label. The main engine worked because `HOOKPROBE_MODEL`
   is a tier that maps through `ANTHROPIC_DEFAULT_OPUS_MODEL`; the subagent
   knob skipped the mapping and was rejected client-side
   (`unrecognized_model`). Every delegation died before a request was made.
   Spotted by the sibling session working the same deployment, from one log
   line on a different query path — an evidenced hypothesis, which is why the
   fix was run as an EXCLUSION TEST rather than adopted as an explanation.
2. **The criterion grepped for the wrong word.** The audit records a
   delegation as an `Agent` entry (plus `TaskOutput` reads), not `Task`. Had
   the knob been healthy all week, the check as written would still have
   reported zero.

The exclusion test: knob set to `claude-opus-5` (same final model through the
tier mapping — behaviour-identical in the world where the hypothesis is
false), one run explicitly ordered to delegate two independent trivial
checks. The audit recorded two `Agent` calls, two `TaskOutput` reads, and the
subagents' own `ls` and `df`. First time asked, it worked.

## Consequences

- The window restarts 2026-08-24 with a working knob and the corrected
  criterion (`Agent`/`TaskOutput` entries from non-patrol runs). Next read:
  2026-08-31. Zero is once again allowed to mean "not worth using" — it just
  was not allowed to mean that while the tool could not physically fire.
- A number that decides a deletion has to be audited before it executes.
  This one nearly deleted three roles for being unused while they were
  unusable — indistinguishable outcomes, opposite conclusions.
- Same class as the wake_someone return-leg bug found hours earlier: a value
  computed correctly and dropped silently on the way to where it acts. The
  common lesson is to verify at the POINT OF CONSUMPTION (the audit log, the
  delivered payload), never at the point of production.

## What would change the answer

A week of healthy-knob zeros. Then the roles go, cleanly, and this note is
the record that the deletion was earned rather than inherited from a typo.
