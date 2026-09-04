# hookprobe — what a run costs, and what it reports about the cost

An agent run is the expensive part of this family, so the levers on that cost
and the record of having spent it are one subject. Nothing here is required to
operate the service; all of it is required to argue about the bill.

## What a run costs, and why reuse is the lever

Each turn shows what it cost: model, tokens in and out, cache reads, dollars,
seconds. The session's running total sits beside the session key, and the header
chip carries the window's spend and its **cache %** — how much context was reused
rather than paid for again.

The other half of that arithmetic is **whether any of it helped**, which nothing
measured. Cost was countable to the cent from the first commit and worth was
countable nowhere, so the first question anyone asks about adopting this — *you
want me to pay a model per alert?* — had a dollar figure and nothing to set
against it. A delivered report now carries **Found the cause** / **Missed it**
buttons, `GET /v1/budget` reports `investigations`, `ruled_useful`,
`ruled_useless` and the sentence assembled from them, and the console prints it
under the spend bars: *12 investigations, $3.42, 5 found the cause*. Only a
person can supply that side, so an investigation nobody ruled on stays unrated —
never counted as a miss.

That number matters more than it looks, because the part of the bill you cannot
negotiate is large. An investigation carries roughly 29k input tokens before its
alert is even mentioned, and measured on a running container it does not shrink:

| configuration | input tokens |
| --- | --- |
| preset `claude_code` + 12 tools | 26,920 |
| no preset + 12 tools | 25,569 |
| no preset + 4 tools | 25,569 |

The preset is worth ~1,350 tokens and cutting the tool list changes nothing —
`allowed_tools` gates execution, not what is sent. Everything of ours (memory,
appended methodology, skills, roles) came to under 3% of the prefix on the
deployment this was measured on.

So the difference between an expensive week and a cheap one is reuse, and three
habits decide it:

- **Follow up in the session rather than opening a new investigation.** Measured
  pair on the same context: $0.1490 fresh, $0.0156 on the follow-up.
- **Let bursts run together.** Concurrent runs share a warm prefix, so lowering
  `HOOKPROBE_MAX_CONCURRENT` to save money does the opposite.
- **Batch edits to memory, prompt and skills.** Each change starts the prefix
  over; adjusting them between alerts pays the full entry fee every time.

Whether to pay that entry fee at all is decided upstream — `HOOKPROBE_ESCALATE_LEVELS`,
the event door's idempotency, and the budget breaker. Caching only sets the
discount. The reasoning and the rejected alternative (a timer to keep the cache
warm — it costs about ten times what it saves at this volume) are in
[.agents/notes](../../.agents/README.md).

## Loop hygiene — the run's context and bill

Two advisory guards, neither a security boundary. They exist because an
unattended run cannot notice it is wasting itself.

**Repeat reminder.** Running an identical call again returns the same answer at
the same price. On the `HOOKPROBE_REPEAT_REMINDER_AT`-th identical call (and
every multiple after) the result carries a note telling the agent to change
approach or record what stays unknown and move on. The budget breaker stops
spending after the fact; this is the nudge before it costs.

**Per-command deadlines.** `HOOKPROBE_BASH_TIMEOUT_MS` and
`HOOKPROBE_BASH_MAX_TIMEOUT_MS` become the CLI's `BASH_DEFAULT_TIMEOUT_MS` /
`BASH_MAX_TIMEOUT_MS`, so a `curl` at an unreachable host or a `kubectl` at a
wedged API server cannot hold a run slot until the whole run times out. The
defaults match the CLI's own — this is a lever to tighten, not a change of
behaviour.

Every run also records **what the model was actually given**: model, skill
layers and their names, subagent roles, MCP servers, and content digests of the
environment memory and the appended methodology. Those files live on a mutable
volume, so without that record a report cannot be explained after the volume
moves on — a stale line in `CLAUDE.md` once made every report come back in the
wrong language while the request looked identical. It sits on each run and each
turn (`inputs`), and the console's session view shows it.

Both guards and the inputs record are borrowed from
[DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)
(`packages/guard`, and its "model-visible means logged" rule). Its third idea,
spilling oversized tool output to a file, was built here and then removed: the
Claude Code harness already does exactly that, and a second layer on top wrote a
file that only held the harness's already-truncated copy while claiming to hold
everything — see
[the rejected note](../../.agents/notes/rejected/2026-08-14-tool-output-spill-in-hookprobe.md).

## Model-call telemetry (on by default, no backend assumed)

Every run's totals are already on its record — cost, tokens, per-model
breakdown, duration, and the tool steps. What that cannot show is the *shape* of
a run: how many model calls it took, which one was slow, where the context went,
that one investigation spent on two different models.

The bundled CLI emits all of that itself over OpenTelemetry, and the SDK merges
this container's environment into the CLI's, so it is configuration rather than
code. It is **on by default** because it costs nothing when nobody is listening —
measured, not assumed: with no endpoint set, a run finishes in the same time,
retries nothing and logs nothing. The only decision left is where to send it:

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://your-collector:4317   # that is the whole setup
```

No default endpoint, no vendor. Point it at whatever you already run, whenever
you get to it.

One event per model call, `claude_code.api_request`:

```
model: "deepseek-v4-pro"      input_tokens: 25853     output_tokens: 2
cost_usd: 0.129315            duration_ms: 1752       cache_read_tokens: 0
session.id: 1a09123d…         prompt.id: bcf7d8b9…    event.sequence: 1
query_source: "sdk"           effort: "high"
```

Alongside it: `assistant_response`, `tool_decision`, and the counters
`claude_code.token.usage` / `cost.usage` / `tool.execution` / `subagent.spawn` /
`mcp.rpc` / `compaction` / `active_time.total`.

This is where per-model detail appears that a run record cannot show: one
investigation was observed spending on **two** models — a fast one for cheap
turns and the main one for the reasoning — while the run's own total is a single
number. Anyone re-pricing usage needs both names.

### What is redacted, and by whom

The CLI substitutes `<REDACTED>` for content in these events — nothing in this
repository does that — under five independent switches. Measured on 2.1.229:

| switch | exposes | default here |
| --- | --- | --- |
| `OTEL_LOG_ASSISTANT_RESPONSES` | the model's answers (`assistant_response.response`) | **on** |
| `OTEL_LOG_USER_PROMPTS` | the prompt — *and* the answers with it | **on** |
| `OTEL_LOG_TOOL_DETAILS` | tool arguments | **on** |
| `OTEL_LOG_TOOL_CONTENT` | whole tool outputs | **on** |
| `OTEL_LOG_RAW_API_BODIES` | raw request/response bodies per call | off |
| `CLAUDE_CODE_OTEL_CONTENT_MAX_LENGTH` | lifts the 60 KiB cap on those raw bodies | off |

Four of the five are on, which is a deliberate posture and not a default to drift
into: everything the agent was given and everything it produced leaves the
container in cleartext once an endpoint is set — including whatever a tool
happened to print, credentials included. The fifth is off because it is the one
that pays most and delivers least (below). It buys the material that cost and token
counts cannot give you: what the model was actually asked, and what it actually
answered. Note the asymmetry between the first two: enabling prompts also enables
answers, but not the reverse.

**The raw bodies are capped at 60 KiB.** The CLI truncates the attribute and says
so in the value (`[TRUNCATED - Content exceeds 60KB limit]`) while `body_length`
keeps reporting the true size, so the loss is visible rather than silent. Every
real investigation exceeds it — one call here carried 113 KB. Because a request
body is `{model, messages, system, tools}`, what falls off the end is the tool
schemas first and the conversation only after that.
`CLAUDE_CODE_OTEL_CONTENT_MAX_LENGTH` lifts the cap (measured: 113426 declared,
113425 received, valid JSON through the closing brace) and is left unset here —
uncapping on top of five open switches would push the whole conversation on every
call unconditionally. Set it when you want byte-exact replay. Note the unprefixed
`OTEL_CONTENT_MAX_LENGTH` also exists and does *not* affect these bodies.

Two things to know before you build on it, both measured rather than assumed
(CLI 2.1.229):

- **These are events, not spans.** `OTEL_TRACES_EXPORTER` is accepted but no
  spans were emitted for a plain run, and the events carry no `traceId`. A call
  tree is reconstructable from `session.id` + `prompt.id` + `event.sequence`; it
  does not arrive as one.
- **`cost_usd` prices Claude models.** On another provider reached through the
  Anthropic dialect it is an over-estimate — the run above reports $0.129 for a
  DeepSeek call. The event carries the real model name and the raw token counts,
  so re-price from those and treat the field as a relative signal.
