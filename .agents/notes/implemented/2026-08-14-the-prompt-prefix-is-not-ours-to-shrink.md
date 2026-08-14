---
title: The prompt prefix is the harness's, so watch reuse instead of trimming
status: implemented
date: 2026-08-14
scope: hookprobe
---

## Decision

Do not try to shrink what an investigation carries before its alert. Measure
whether that fixed prefix is being reused, and show the number where an operator
will see it: per session on the header line, per window on the header chip, from
`/v1/budget` (`input_tokens`, `cache_read_tokens`, `cache_hit_ratio`).

## Why

An investigation on this deployment pays for ~29k input tokens before the alert
is even mentioned, and that is where the money is: across 38 recorded turns in
production, the *first* call of each investigation cost $15.53 in total, median
29,008 input tokens.

The obvious move is to trim it. Measured on the running container, that move is
not available:

| configuration | input tokens |
| --- | --- |
| preset `claude_code` + 12 tools | 26,920 |
| no preset + 12 tools | 25,569 |
| no preset + **4** tools | 25,569 |

The system-prompt preset accounts for ~1,350 tokens, and cutting the tool list
from twelve to four changes **nothing** — `allowed_tools` gates execution, not
what is sent, so the schemas go out either way. Our own contributions are
smaller still: on production `CLAUDE.md`, `system-prompt.md` and the skills
directory are empty, and three role files come to ~709 tokens, under 3% of the
prefix. There is nothing here to cut.

What does vary is reuse. Over those same 38 turns: 1,155,118 fresh input tokens
against 1,416,320 read from cache — a 55% hit ratio — but only 14 of 38
*first* calls hit anything. Follow-up turns inside a session almost always hit
(one measured pair: $0.1490 fresh, $0.0156 on the reuse). So the difference
between an expensive week and a cheap one is whether work lands in an existing
session and close in time, and that is a thing an operator can influence — once
they can see it.

Note the provider caches implicitly here: `cache_creation_input_tokens` is always
zero while reads are large, so the honest ratio is reads over reads-plus-fresh.
A ratio computed against writes would read as a permanent 0%.

## Consequences

- The lever an operator actually has is behavioural, and the console now supports
  it: follow up in an existing session rather than opening a new investigation;
  let bursts run together rather than lowering `HOOKPROBE_MAX_CONCURRENT`, since
  concurrent runs share a warm prefix; batch edits to memory, prompt and skills
  instead of adjusting them between alerts, because each change invalidates the
  prefix.
- Cost control proper lives upstream of caching: `HOOKPROBE_ESCALATE_LEVELS`, the
  event door's idempotency, and the budget breaker decide whether to pay the ~29k
  entry fee at all. Caching only decides the discount.
- If the harness later exposes what it sends — a smaller tool set on the wire, or
  a non-coding system prompt — the measurement above is the one to repeat, and
  the table is here to compare against.
- Deliberately rejected on the way:
  [keeping the cache warm on a timer](../rejected/2026-08-14-a-timer-to-keep-the-prompt-cache-warm.md).
