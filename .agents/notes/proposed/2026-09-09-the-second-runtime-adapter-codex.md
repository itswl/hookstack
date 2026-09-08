---
title: Codex as the second runtime — what the contract caught, and what only a live run caught
status: proposed
date: 2026-09-09
scope: hookprobe
---

## Decision

`service.Engine` has a second implementation. `HOOKPROBE_RUNTIME=codex` drives
`codex exec --json` as a subprocess; `claude` remains the default and nothing
about an existing deployment changes. The conformance suite the parking note
asked for exists as `hookprobe/tests/test_runtime_contract.py`, and
`hookprobe/runtimes.py` is the registry it reads, so a third adapter cannot be
added without appearing in the thing that judges it.

## Why the posture moved before the adapter arrived

The decision — which call is refused, why, and what gets recorded — now lives in
`hookprobe/gate.py`, and both adapters call it. The Claude adapter registers it
as in-process hooks; Codex spawns `python -m hookprobe.gate` per tool call.

Writing it twice was the obvious path and it is the one failure this whole
exercise exists to prevent: `readonly` would have meant one thing on a node
running one engine and something slightly else on a node running the other, and
nothing in either suite would have noticed the day they diverged. The module is
kept deliberately cheap to import, because the spawned mechanism pays that
import on every tool call.

Codex made this easier than it might have been: its `PreToolUse` payload
carries `tool_name` and `tool_input.command` under exactly the names Claude Code
uses, and `{"permissionDecision": "deny"}` is read the same way.

## What the contract caught, and what it did not

Recorded fixtures caught the parsing. Nothing else. Every defect that mattered
came from running the adapter once against a real model, and the first one is
the reason `verify_gate` exists:

**The first live run had no gate, and nothing said so.** The hook command could
not import hookprobe — the spawning interpreter had no reason to have this
package on its path — so codex logged the failure, carried on, and
`kubectl delete pod conformance-canary` ran to completion on a node whose
`/v1/agent` was reporting `bash_guard: readonly`. The audit file was empty. Every
layer behaved reasonably and the posture was simply absent.

Two fixes, because one was not enough. `PYTHONPATH` now carries the package, and
before its first turn a node **spawns its gate exactly as the runtime will,
hands it a call no posture permits, and refuses to start unless it is refused**.
A gate that fails to launch is not a gate, and codex does not fail closed when a
hook command errors — so the check has to be ours.

The conformance test that should have caught it did not, and the reason is worth
keeping: it put `sys.path` into the subprocess `PYTHONPATH`, and so proved only
that the gate works when somebody else makes it importable. **A test that helps
the thing it is testing is a test that will let the real failure through.** It
now uses the adapter's own command and the adapter's own environment, with
nothing added.

**A successful turn was recorded as failed.** Codex has no warning channel and
emits notices as `error` items, including one about hook trust on every single
run. This is the third time this repository has learned the same thing: a turn
that produced an answer did not fail. The `error` items go to the feed; the
verdict comes from the return code and whether an answer exists.

**Resume came back silently empty.** `codex exec resume <id> --sandbox …` is
refused outright by the CLI: the flags have to precede the subcommand. It failed
as an empty run rather than an error, which is the worst shape a failure can
take.

## What codex gives, and what it costs

Better than expected on three of the five obligations. `thread.started` is the
**first line** of the stream, so the resumable id arrives before anything else
without the adapter digging for it. `codex exec resume` reads transcripts off
disk, so a session genuinely outlives a boot — proven by a separate process
resuming a thread and recalling what the first one was told. Hooks fire for
subagents too, which is the whole reason the audit can claim to cover calls the
message stream never carries. And `--sandbox read-only` is an OS-level refusal
sitting underneath the guard, which the Claude adapter has no equivalent of.

Two real costs, both now written into `docs/containment.md` and
`hookprobe/docs/runtimes.md` rather than left in a commit message:

**Hook trust is bypassed.** Codex wants a person to approve hook commands and
`exec` has nobody to ask. The flag's own help says it is intended for automation
that vets its hook sources, which this does — the home is built here — but what
it does not stop is anything else that can write into that directory installing
a hook that runs as the probe.

**Cost is always `None`.** Codex counts tokens, never money. That is the honest
answer and the ledger is built for it, but it means the budget breaker cannot
see a codex node's spend. Such a node needs a ceiling on the provider side.

## Consequences

**The contract now judges instead of describing.** That was the point, and the
first thing it judged was the adapter written to satisfy it.

**A node reporting one runtime while running another is now impossible-ish.**
`/v1/agent` reports `settings.runtime` rather than a hardcoded string, and
`build_engine` refuses an unknown name instead of falling back to the default.

**Not deployed anywhere.** No image ships the codex binary, and no deployment
sets `HOOKPROBE_RUNTIME`. This is a capability, not a migration, and the next
person should be clear that running production on it would mean giving up the
budget breaker's view of spend.
