---
title: The Codex Python SDK — better on every axis except the one that matters
status: rejected
date: 2026-09-09
scope: hookprobe
---

## Decision

Evaluated `openai-codex` (PyPI, from the `openai/codex` repository) as the way
to drive Codex, and did not adopt it. The adapter keeps driving `codex exec` and
parsing its JSONL. This note exists because the SDK is the obviously better
choice on every axis but one, so the next person to reach for it deserves the
measurement rather than the conclusion.

The operator asked for the switch. The finding below is the answer, and the
contract is what makes it a finding rather than a preference.

## Why it looked right, and still does

Four things it gives that the subprocess adapter had to work for:

- **`thread.id` before the turn.** Obligation four met by the interface instead
  of by the runtime happening to order its output kindly.
- **`interrupt()` instead of SIGTERM.** The contract's `stop()` says in as many
  words that this is the difference between a recorded cost and a `None` on
  every stop and every restart.
- **`model_context_window` beside the token usage.** The Claude adapter has to
  ask a runtime that will not answer, with a timeout and a latch, after a
  production patrol died on a context limit nothing had warned about. Here it
  arrives unasked.
- **`pip install` ships the CLI binary.** Which was the single thing keeping
  this adapter out of a deployment.

## Why not

**Hooks do not run under `codex app-server`, which is what the SDK drives.**

Measured, against the same `CODEX_HOME` and the same `hooks.json`, with the same
binary:

| driven by | `kubectl delete` | audit line | `hook/started` in the stream |
| --- | --- | --- | --- |
| `codex exec` | refused | written | n/a |
| `codex app-server` | ran | none | absent |

What was tried, all of it against both codex-cli 0.147.0 (the version the SDK
pins) and 0.153.4: `hooks.json` in `CODEX_HOME`; the inline `[hooks]` form in
`config.toml` with `type = "command"`; `[features] hooks = true` in that file;
`--config features.hooks=true` on the launch; and
`--dangerously-bypass-hook-trust` through `launch_args_override`. The same
config fires under `exec` every single time.

**The official documentation says this should work.** `learn.chatgpt.com/docs/
app-server` states that the app-server emits `hook/started` and `hook/completed`
"when a synchronous lifecycle hook starts and when its final run summary is
available", and the protocol carries the types to match. So this is a gap
between documented and observed behaviour rather than a documented limitation —
which is a better thing to have found, because it will close.

An open upstream issue is consistent with it without being the same report:
`openai/codex#21639`, "Hooks no longer run after Codex Desktop update", open
since 2026-05-08, where the app-server-backed desktop stopped running both
`SessionStart` and `PreToolUse` hooks. Nothing found describing the
exec-versus-app-server split directly.

**The approval handler is not a substitute.** The SDK exposes one client-side
decision point, `approval_handler`, answering
`item/commandExecution/requestApproval`. That fires when the sandbox alone
cannot decide a call, not before every tool, so it cannot carry a posture
written in verbs. Its default implementation accepts.

So the SDK buys ergonomics with obligation one. The parking note said an adapter
that cannot supply the gate is a finding and not an obstacle to work around, and
this is that sentence being cashed.

## What the evaluation was worth anyway

It found a hole in this repository's own check.

`gate.verify()` proves the gate **answers**. It does not prove the runtime
**asks**, and from inside the process those look identical. Under the SDK the
gate command was perfect: the self-test passed, `/v1/agent` reported
`bash_guard: readonly`, and `kubectl delete` ran. A posture can be absent
without a single thing being broken.

So there is now a second check. After a turn, if tools ran and the flight
recorder holds no line for that session, the node sets `_gate_broken` and takes
no further turns. It is a stop rather than a warning because the service is
telling operators, `/v1/agent` and every report that the investigation ran
read-only, and one of those statements has become false. Both adapters make the
check; the containment row covers both halves.

## Consequences

**Revisit on a codex release that emits `hook/started` from an app-server
stream.** That single observation flips this decision, and everything else about
the SDK is worth having. The check costs one turn: run one command the guard
refuses and look for a `hook/` method in the stream.

**Two things the official documentation corrected while checking this**, both in
this adapter's favour. `PreToolUse` fires for "Bash, file edits performed
through `apply_patch`, MCP tool calls, and other local function tools" — so the
MCP and input guards are reachable under codex, not only the bash one. And a
hook may also block by exiting 2 with the reason on stderr, which is a second
route this gate does not need but a future one might.

**Only the bash guard has been proven under codex.** The live drills refuse
`kubectl delete`; nothing here has yet watched the input guard refuse a write to
a steering file or the MCP guard refuse a tool, on this runtime. The
documentation says they will fire. That is not the same as having seen it.

**The deployment gap stands.** No image ships the codex binary, and the SDK was
the clean answer to that. Shipping it now means installing the CLI in the image
by hand.

**The docstring that justified the subprocess has been corrected.** It used to
say the app-server was a daemon with a lifecycle to supervise and no answers the
stream did not already give. That was reasoning from the armchair, and both
halves were wrong: the SDK supervises the lifecycle, and it has answers this
adapter wanted. The real reason is the measurement above, and it is what the
file says now.
