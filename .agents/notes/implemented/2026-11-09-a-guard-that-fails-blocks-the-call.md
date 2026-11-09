---
title: A guard that fails blocks the call instead of counting as no decision
status: implemented
date: 2026-11-09
scope: hookprobe
---

## Decision

The three PreToolUse guards of the Claude engine, the bash guard, the input
guard and the MCP guard, are registered with the SDK's `on_failure="block"`.
A guard callback that raises, or answers in a shape the CLI rejects, now makes
the CLI refuse the call. The telemetry hook on the same event, and the audit,
hygiene and compaction hooks on other events, decide nothing and keep the
default.

## Why

The option arrived in claude-agent-sdk 0.2.177, which came in with this
relock. Before it, the SDK treated a failing hook as "no decision", and the
call ran: a guard that crashed on an input nobody foresaw fell open exactly
when it was broken. The guards are written to survive malformed input, and a
test pins that, but a boundary that depends on its own code never raising is
the shape this repository keeps finding: the selftest exists because a gate
that quietly stopped running is indistinguishable from one with nothing to
catch.

The SDK refuses to connect to a CLI that would not honour the option, so the
boundary cannot revert in silence on an older runtime either. Measured on
this change's image: the engine connects and completes a scripted turn with the
bundled CLI 2.1.319, a protected write is still refused, and prompts stay
verbatim.

## Consequences

- A bug in a guard refuses every call it is asked about, until it is fixed.
  That is the right direction for a gate, and it will be visible: the run
  fails its tool calls and says why.
- A CLI older than the option cannot run this engine; the SDK says so at
  connect. The CLI is the one the SDK bundles, so this does not arise here.
- Not exercised: a guard made to raise on purpose, end to end. The unit test
  pins the option on exactly the three guards; the SDK's own tests cover what
  the CLI does with it.
- The codex and pi runtimes spawn the gate as a process and are unaffected.
