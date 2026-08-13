---
title: Guard the run's context and record what the model was given
status: implemented
date: 2026-08-14
scope: hookprobe
---

## Decision

Three additions to hookprobe, all advisory — the security boundary stays the bash
guard plus read-only credentials:

- **Repeat reminder** (`hookprobe/hygiene.py`). A PostToolUse hook counts identical
  `(tool, arguments)` calls within a run and, on the
  `HOOKPROBE_REPEAT_REMINDER_AT`-th (default 3) and every multiple after, returns
  `additionalContext` telling the agent to change approach or record what stays
  unknown and move on.
- **Per-command deadlines.** `HOOKPROBE_BASH_TIMEOUT_MS` and
  `HOOKPROBE_BASH_MAX_TIMEOUT_MS` are passed to the CLI as
  `BASH_DEFAULT_TIMEOUT_MS` / `BASH_MAX_TIMEOUT_MS` through
  `ClaudeAgentOptions.env`. Defaults match the CLI's own, so this is a lever to
  tighten rather than a change of behaviour; `0` leaves the CLI's default alone.
- **Inputs record.** `ClaudeAgentEngine.describe_inputs()` resolves what the run
  will actually put in front of the model — model, setting sources, skill filter
  and the skill names in each layer, subagent roles from files and from config,
  MCP server names, and size plus content digest of the environment memory and the
  appended methodology. The service stores it on the run and on each turn
  (`inputs`), and the console shows it per turn.

## Why

The two guards come from DeepSeek Harness `packages/guard`, which treats them as
loop hygiene: consumers of extension points, deliberately not swappable
capabilities. An unattended run cannot notice it is wasting itself. The budget
breaker already stops spending once a window's ceiling is hit, but that is an
after-the-fact brake on money; a run that calls the same failing command six times
or hangs on one unreachable endpoint burns turns and wall clock before the breaker
has anything to say.

The inputs record comes from the same project's stricter rule, *model-visible means
logged*: anything that reaches a model request must be reconstructable from the
record, enforced there by a runtime invariant. hookprobe assembles its prompt from
files on a mutable volume — `CLAUDE.md`, `system-prompt.md`, distilled skills,
role files, an MCP config read fresh per run. None of it was recorded. That is
exactly how the first English-language report came back in Chinese: the memory file
on the volume still said otherwise, the request looked identical, and nothing in
the result explained the difference. Digests rather than contents keep investigation
instructions out of every result file while still proving which text was loaded.

## Consequences

- A report can be explained after the volume has moved on: the turn names the
  skills, roles, MCP servers and memory digest that produced it.
- `Engine` (the Protocol in `service.py`) gains `describe_inputs`, so any future
  engine must be able to say what it feeds the model. Failure to produce the record
  is caught and leaves `inputs` empty rather than failing the run.
- The reminder is text in the model's context, not a stop: a determined loop still
  loops. Enforcement would need a stop decision, which is a separate decision to
  take when a real run shows the reminder being ignored.
- Deadlines cannot distinguish "hung" from "legitimately slow". Anything that must
  outlive the default has to ask for a longer timeout, up to the maximum.
- Its sibling idea, spilling oversized output ourselves, was tried and removed:
  see [the rejected note](../rejected/2026-08-14-tool-output-spill-in-hookprobe.md).
