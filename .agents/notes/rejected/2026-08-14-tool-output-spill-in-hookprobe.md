---
title: Do not spill oversized tool output ourselves — the harness already does
status: rejected
date: 2026-08-14
scope: hookprobe
---

## Decision

hookprobe does not persist or truncate oversized tool output. A PostToolUse hook
that replaced large results with a bounded preview plus a file locator was built,
verified against a live run, and removed the same day.

## Why

The idea came from DeepSeek Harness `packages/spill`, which is sound in a harness
that owns its own tool pipeline. Claude Code already owns that pipeline here, and
the live run showed the layering plainly. Asking the agent to run `seq 1 60000`
produced 340.7 KB of stdout, and what reached the model was:

```
<persisted-output>
Output too large (340.7KB). Full output saved to:
  /data/home/.claude/projects/-data/<session>/tool-results/bb3dmvfl4.txt

Preview (first 2KB):
[hookprobe] Output was 30000 characters, too large to keep inline.
Full output: /data/spill/probe_hygiene_1/20260813-174318-Bash-1.txt — Read or Grep …
```

Three facts fall out of that, in order of severity:

1. **Our "full copy" was not full.** The harness caps the `stdout` a hook sees at
   exactly 30 000 characters, so the file we saved held 6 221 of the 60 000 lines
   while its own message promised the rest was there. The agent believed it and
   reported 6 221 lines as the output — a wrong answer produced by our guard.
2. **The harness already saves the real thing.** Its `tool-results/` copy is the
   complete 340.7 KB, and its message already hands the agent that path.
3. **Our preview was discarded anyway.** The harness truncates to a 2 KB preview
   *after* the hook runs, so the head-and-tail structure we carefully built was
   cut mid-way. The tail we preserved on purpose never reached the model.

A second layer over a mechanism that already exists cannot be better than it, and
this one was measurably worse.

## Consequences

- Nothing in hookprobe reads or writes `{workdir}/spill`, and no retention target
  covers it. A directory left behind on an existing volume is inert.
- `HOOKPROBE_SPILL_LIMIT_BYTES` does not exist. Output budgeting belongs to the
  harness; if it ever needs tuning, the lever is the harness's own setting, not a
  hook of ours.
- The sibling guard from the same borrowing, the repeat-tool reminder, stays: it
  has no upstream equivalent and the same live run showed it firing correctly on
  the third identical call.
- The general lesson, worth more than the feature: when borrowing from another
  harness, first check whether the harness you actually run already does it. The
  test that settled this took one live run and would not have shown up in any
  unit test, because the truncation happens outside the process under test.
