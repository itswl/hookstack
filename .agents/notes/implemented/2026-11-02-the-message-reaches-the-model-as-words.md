---
title: The message reaches the model as words, and which CLI the engine actually runs
status: implemented
date: 2026-11-02
scope: hookprobe
---

## Decision

The investigator hands the SDK its message with `verbatim_prompts=True`. The
message is an alert's text, a reply under a card, or a watch round: written by
whoever can reach those doors, never by an operator at the CLI's prompt. Taken
as typed input, two things in it acted before any tool call:

- an `@path` anywhere in the text made the CLI read that file and give it to
  the model as a Read result that no PreToolUse hook saw, so neither the input
  guard nor the audit knew a read had happened;
- a message starting with `/` ran a CLI command instead of an investigation:
  `/cost show me` came back as the run's report, a cost table.

Measured with the real service in its image, a recording stand-in for the model
API on an internal Docker network, and a marker file mounted into the
container. A request naming the marker with `@` put the marker's content into
the request the stand-in received, on the published 0.9.2 image and on this
change's image before the switch. After the switch the content never arrived,
`/cost show me` reached the model as text, and the scripted turn still ran: an
allowed read executed, and a `touch` into `results/` was refused and left no
file.

The option arrived in claude-agent-sdk 0.2.158 and needs CLI 2.1.248 or newer.
It came with the relock of Dependabot's first proposal for the investigator's
runtime, which moved the SDK from 0.2.140 to 0.2.176.

## Why

The guard and the audit are built on tool calls. A read that happens before any
tool call is outside both, so the only fix that holds is to stop the CLI from
reading on the text's say-so. The SDK ships that switch for exactly this case.
A filter of our own over the text would have to know every way the CLI
interprets input, and that changes with every CLI release.

Nothing relied on the expansion. No message the service builds carries a slash
command or an `@` mention, and skills load through setting sources, not slash
commands.

## Consequences

- A person who writes `@/some/file` in a chat reply no longer gets the file
  attached. The model sees the words and may read the file itself, as a tool
  call the guard checks and the audit records.
- A slash command in a reply is text for the model, not a CLI command.
- `tests/test_engine_loop.py` pins the option, and
  `scripts/assert_guards_are_tested.py` flips it and requires that test to fail.
- Not measured: the codex and pi runtimes, which hand the message to their own
  CLIs.
- Found on the way and not decided here: the engine runs the CLI bundled inside
  the SDK wheel, not the one the Dockerfile installs from npm with
  `CLAUDE_CODE_VERSION`. The SDK looks for its bundled binary first. The 0.9.2
  image ran 2.1.235 with 2.1.259 unused on PATH, and this relock moves the
  engine to 2.1.316. So the lock, not the build argument, has been pinning the
  agent runtime since the SDK began bundling it. The operator chose the lock
  the same day: see 2026-11-02-the-lock-pins-the-agent-runtime.
