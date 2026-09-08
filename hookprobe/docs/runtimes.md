# Runtimes

An investigation runs on an agent runtime. Which one is `HOOKPROBE_RUNTIME`:
`claude` (the default, the Claude Agent SDK), `codex` (the Codex CLI) or `pi`.
The choice is per node, and a deployment can run one of each.

This page is about what a runtime has to be able to do before it is allowed to
run one of these nodes, because the interesting part is not that a second
adapter exists. It is that everything this service claims about itself is a
claim about the runtime.

## The contract, and the two obligations that are invisible

`service.Engine` is a Protocol with five obligations, and its docstring is the
authority. Three are visible in the signatures — a session id that outlives the
process, an incremental event stream, cost or `None`. Two are not:

**A tool gate that runs BEFORE a tool does.** `readonly` is not a sentence in a
prompt. It is a decision made before the shell runs, and a runtime that can only
be *asked* not to write cannot be run under that word.

**A per-call audit record the agent cannot edit**, including inside subagents,
whose calls never appear in the message stream. Without it a run's account of
itself is the run's own word.

A candidate that cannot supply those two is a finding, not an obstacle. The
honest thing is to say so rather than keep the word and lose the boundary.

## One posture, two mechanisms

`hookprobe/gate.py` holds the decision — which is refused, why, and what gets
recorded. It is deliberately cheap to import, because the second mechanism pays
that import on every tool call.

| runtime | how the gate is reached |
| --- | --- |
| `claude` | in-process `PreToolUse` hooks calling `gate.deny_reason` |
| `codex` | `hooks.json` spawning `python -m hookprobe.gate`, one line of JSON each way |
| `pi` | a shipped extension, `pi_gate.ts`, shelling out to the same command |

The mechanisms differ; the decision does not. Two copies of it would mean
`readonly` quietly meaning one thing on one node and something else on another,
with nothing in either test suite noticing.

## Codex

`codex exec --json` is driven as a subprocess. `thread.started` is the first
line of the stream, which is where the resumable session id comes from; the
prompt goes in on stdin; `codex exec resume <id>` continues a thread from a
previous boot.

Set up:

```
HOOKPROBE_RUNTIME=codex
HOOKPROBE_CODEX_CONFIG=/run/secrets/codex-config.toml   # your provider + credential
HOOKPROBE_CODEX_PYTHON=/app/.venv/bin/python            # must import hookprobe
HOOKPROBE_MODEL=<the model id your provider serves>
```

The node builds a `CODEX_HOME` it owns under the workdir, copies that config in
(read, never written — credentials stay in the operator's file), writes its own
`hooks.json`, and keeps thread transcripts there, on the same volume that makes
a restart resumable.

Every spawned-gate runtime proves its gate before its first turn: see
[the self-test](#every-node-proves-its-gate) below.

### Every node proves its gate

**Before its first turn a node proves its gate.** It spawns the gate exactly as
the runtime will and hands it a call no posture permits; if the answer is not a
refusal, the node does not start. This is not ceremony. The first live run of
this adapter had no gate at all: the hook command could not import hookprobe,
codex logged the failure and carried on, and a `kubectl delete` ran to
completion on a node whose `/v1/agent` was reporting `bash_guard: readonly`.
Every layer behaved reasonably and the posture was simply absent.

Two things to know before running one:

**Hook trust is bypassed.** Codex asks a person to approve hook commands once,
and `exec` has nobody to ask. The adapter passes
`--dangerously-bypass-hook-trust` and vets the source by being the thing that
wrote it. What that does not stop is anything else able to write into that
directory: a process that can put a file in the run's volume installs a hook
that runs as the probe. It is a row in
[containment](../../docs/containment.md) for that reason.

**Cost is always `None`.** Codex reports tokens, not money. The ledger keeps
"nobody counted" and "this was free" apart and the budget breaker reads the
difference, so a plausible 0.0 would corrupt both. Token counts are kept in
`usage`, where the weekly account can price them the day it learns how. A node
on codex is a node whose spend the breaker cannot see — run it under a provider
that has its own ceiling.

## pi

`pi --mode json --print` is driven as a subprocess. The stream's **first line is
the session header**, which is where the resumable id comes from; `--session
<id>` continues a session from a previous boot; transcripts live under the
node's own directory.

Set up:

```
HOOKPROBE_RUNTIME=pi
HOOKPROBE_PI_CONFIG=/home/probe/.pi/agent   # your models.json and auth.json
HOOKPROBE_PI_PYTHON=/app/.venv/bin/python   # must import hookprobe
HOOKPROBE_PI_PROVIDER=<the provider serving your model>
HOOKPROBE_MODEL=<the model id that provider serves>
```

The gate is `pi_gate.ts`, shipped in this package and loaded with `--extension`.
It holds no policy: it translates pi's lower-case tool names into the ones the
guards speak, calls `python -m hookprobe.gate`, and turns the answer into pi's
`{ block, reason }`. A node whose extension is missing does not start, because
pi has no sandbox of its own — the extension is the whole posture.

`--no-approve` is passed on every run. Project-local pi settings and extensions
are attacker-influenced here: the workspace holds files previous runs wrote.

Two differences from codex worth knowing, because they are the ones an adapter
written from signatures alone would get wrong:

**A refused call still appears.** Codex omits the tool entirely when the gate
says no. pi announces the call, preflights it, and ends it carrying the refusal
as its result, so the step exists and is marked as failed. That is arguably the
better record — it shows what the agent tried — and the process feed reads the
same either way.

**Cost is reported, which is the trap.** pi prices a turn from its model
catalog. A model served through a private gateway is not in that catalog, so
`usage.cost.total` comes back `0` beside several thousand real tokens — exactly
the shape the ledger forbids, and a budget breaker fed those numbers would watch
an unattended node spend all week and see nothing. A zero with tokens behind it
is therefore reported as `None`; only a zero with nothing spent means free. When
the model IS in the catalog the real figure is passed through, which makes pi
the only non-Claude runtime here whose spend the breaker can see.

## Adding another

`hookprobe/tests/test_runtime_contract.py` is the suite, and
`hookprobe/runtimes.py` is the registry it reads: a runtime that the suite does
not judge fails the suite. Add the row, run it, and let it tell you which of the
five obligations the candidate does not meet.

Write the parsing so it can be driven over a recorded stream, the way `_Turn`
is. The parsing is the part of an adapter most likely to be wrong, and finding
that out should not cost a paid model run — but do not stop there. Every defect
in the Codex adapter that mattered was found by running it once against a real
model, and none of them would have been found by a fixture.
