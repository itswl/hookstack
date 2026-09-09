---
title: Four things borrowed from the OpenAI Agents SDK — three built, one deferred with its architecture problem named
status: proposed
date: 2026-09-09
scope: hookprobe
---

## Decision

`openai/openai-agents-python` was **not** adopted as a runtime — it is a
framework for assembling an agent, not a CLI-shaped agent to drive, so it does
not fit behind `service.Engine` without hookstack supplying its own bash, read
and grep tools. That is weeks of work for a claim three adapters already make.

Four of its ideas were worth taking. Three are built:

* **A test double that counts the calls** (`tests/helpers.ScriptedTurns`, from
  `agents.testing.ScriptedModel`). It found a live bug on its first run.
* **A tool OUTPUT guard** (`gate.output_reason`, from `ToolOutputGuardrail`).
* **A third gate outcome, counted** (`gate.trips` +
  `EngineResult.guard_trips`, from `ToolGuardrailFunctionOutput`).

The fourth is deferred, and this note is where its architecture problem is
written down rather than discovered later.

## Why each

**The double, because the gap had already been paid for twice.** `FakeEngine`
is injected at the `Engine` boundary and never enters the receive loop, and
`test_engine_loop.py` records what that cost: two bugs in one day, both one
assertion away. Its own fake closed half of it by scripting a stream. What was
still missing is the property `ScriptedModel` has — **failing when the loop
makes a call nobody scripted, and when scripted steps go unconsumed**. On a
product whose central argument is cost, a turn asked twice is a turn billed
twice, and no test in this suite could see it.

It earned itself immediately: `_context_usage(client)` sat AFTER the `finally`
that disconnects, so the probe always ran against a closed client. It could not
succeed on any runtime; the latch then recorded "this runtime does not report
context usage" and every run's `context` was null — a conclusion about the
runtime drawn from our own call order.

**The output guard, because the input guard was only ever half the question.**
Measured: `kubectl get secret db-creds -o yaml` and `env` are ALLOWED under
`readonly`, and should be — the posture refuses changes, and an investigator
that cannot read cannot investigate. But nothing looked at the answer, so a run
whose context had been filled with a live credential was indistinguishable from
one that read a pod list. Nobody could decide to discard the report or rotate
the key. It records the KIND and never the match, because the reason string
reaches an audit file and sometimes a chat card.

**The third outcome, because nothing escalated.** `deny_reason` could refuse and
continue, and that was all. An agent steered into the posture twenty times left
the same trace as one refused once and rephrased. Counted from the audit,
because the gate is stateless where it is spawned per tool call.

## Deferred: approval per command, on the tool

`ShellTool` carries `needs_approval` and `on_approval`, so the runtime asks a
person mid-call and continues on the answer. That is attractive here for one
node specifically: the work runner is `danger-only`, and its own note is honest
that this "is not a sandbox and not pretending to bound what the runner may do
— what bounds it is the credential mounted in". Today its bash is a blocklist:
refuse the handful of verbs that destroy a machine, allow everything else. An
approval callback would let it **ask** instead of refuse, which is this stack's
own doctrine applied one level lower.

**It is deferred because the run model does not have a place to wait.** Every
approval here is out-of-band by design: the agent proposes in words, the
service acts, and a person answers a card minutes later. A mid-call approval
requires the turn to BLOCK on an answer that arrives through the pipe, the
bridge and a human — while holding a run slot, a model context and a budget
window open. That is not a hook, it is a new state in the run machine, and
every bound the loop has (wall clock, slot count, budget window, one resume per
run) is written on the assumption that a turn does not stop and wait.

What to build first if it is wanted: not the callback. The measurement — how
often `danger-only` actually refuses on the work runner. `guard_trips` now
records exactly that, so the case can be made from numbers instead of
architecture.

## Not available, and one thing to avoid

**Per-request usage is not on offer.** `Usage.request_usage_entries` attributes
tokens per model call; the Claude SDK reports a per-turn total plus
`model_usage`, which this stack already keeps. Nothing to borrow without the
runtime exposing it.

**Its tracing is on by default and exports to the vendor.** Nothing left this
machine only because `OPENAI_API_KEY` was unset — the SDK logs "skipping trace
export" and carries on. On a node holding that key, prompts, tool arguments and
outputs would be shipped to OpenAI's trace store with no line of configuration
having asked for it. `set_tracing_disabled(True)` is the switch. Recorded as a
contrast rather than a criticism: this stack's OTel content switches are OFF by
default, and a 29k-star official SDK choosing the other default is the argument
for keeping ours.

## Consequences

**One bug fixed, two invisible things now recorded, and no new dependency.**
Nothing in this borrow imports the SDK — the shapes were read, the ideas taken.
The double lives in `tests/helpers.py`, the guards in `gate.py`.

**`guard_trips` is recorded and not acted on.** Failing a turn past a threshold
would change behaviour on upgrade, and a legitimate narrowing is refused a few
times on the way. Measure, then gate — the same order the routing vocabulary
and the automation graduation used.

**The output guard is a detector, not a gate, and says so.** By PostToolUse the
answer has already reached the model, so a denial there is theatre, and no shape
for making a runtime withhold it is agreed across all three adapters.
