---
title: pi (pi-mono) as the probe's engine, re-evaluated and declined again
status: rejected
date: 2026-08-18
scope: hookprobe
---

## Decision

The engine stays the Claude Agent SDK. pi — first weighed and set aside on
2026-08-11, when it was an interactive-first TypeScript harness — was
re-evaluated on 2026-08-18 against what it has since become, and declined
again, this time with the capability table written down.

## Why

pi in 2026-08 is a much stronger candidate than it was: an RPC mode (JSON over
stdio, built for non-Node hosts) and an SDK mode beside the TUI, a
permission-gate example in its extension system, tree-shaped resumable
sessions, 15+ providers natively, MIT, and very wide adoption. The embedding
architecture would be the same shape we already run — a Python service driving
a Node subprocess — so "it's a TS stack" is no longer the objection it was.

The objection now is a philosophy collision, visible in pi's own docs: pi
*deliberately* ships without MCP, without subagents, and without a built-in
permission layer — "build an extension" is the official answer to each. The
probe *uses all three, load-bearing*:

| capability | on the SDK | on pi |
| --- | --- | --- |
| tool-call veto (bash guard, input guard) | Python `PreToolUse` hooks, apply inside subagents | a TypeScript extension; the security rules split across two languages |
| audit + step timing | Python `PostToolUse` hooks, one JSONL line per call | same extension work again |
| SKILL.md runbooks (the learning loop's output) | loaded natively, layered project/user | pi "skills" are CLI-tools-with-READMEs, loaded on demand — a different mechanism; the loop's write side would need reshaping |
| MCP servers (`HOOKPROBE_MCP_CONFIG`, hot-read per run) | native client | an extension that adds MCP |
| parallel sub-investigations (Task) | native, hooks bind inside them | "tmux or custom extensions" |
| session resume (`/continue`) | native | native (session trees) |
| providers | Anthropic dialect only — solved once via DeepSeek's `/anthropic` endpoint | 15+ native, genuinely better |
| per-run usage/cost accounting | full (`usage`, `model_usage`, `cost_usd`) | not documented |

Migration would spend two to three weeks re-implementing the four guard layers
as TypeScript extension code — moving the security boundary's implementation
out of the tested Python package and splitting it across a language border —
to arrive at feature parity, plus exactly one real gain: native multi-provider,
which the Anthropic-dialect trio already solved for the one provider in use.
The probe replaced OpenClaw partly for being heavy; pi is OpenClaw's own
lineage, and adopting it would be walking back toward the thing the probe
exists to be smaller than.

On the "does the SDK look unprofessional" question that prompted the
re-evaluation: supplying the loop from a harness is the current mainstream
engineering position, and this repository's version of it is deliberate and
enforced — `engine.py`'s docstring states the engine owns no loop, the service
depends on an engine *interface* (`run`/`describe_inputs`), and the entire
test suite runs against `FakeEngine`/`GatedEngine` without importing the SDK.
What reads as professional is not which harness drives the loop; it is that
the choice is written down, bounded, and swappable — which this note is part
of.

## Consequences

- The engine seam must stay honest: anything SDK-specific stays inside
  `ClaudeAgentEngine`; the service and tests keep depending on the interface
  only. `_hook_list` marks the exact type boundary.
- Provider independence continues to mean "any Anthropic-dialect endpoint",
  not "any provider". Acceptable while DeepSeek ships one; the day the chosen
  provider has no Anthropic dialect is the day this note gets revisited.
- Revisit triggers, concretely: the SDK's terms or pricing make embedding
  untenable; the probe stops needing MCP/subagents/SKILL.md (unlikely — the
  learning loop writes SKILL.md); or pi grows a host-side tool-veto in RPC
  mode, which would collapse the biggest row in the table above.
