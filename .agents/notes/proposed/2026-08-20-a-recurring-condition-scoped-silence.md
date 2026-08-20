---
title: A recurring, condition-scoped silence — wanted by the nightly patrol, does not exist
status: proposed
date: 2026-08-20
scope: hookrelay
---

## Decision

Not built. The nightly silence patrol
(`hookprobe/examples/patrols/nightly-silence-proposal.md`) can identify a
condition that fires in the same hour every night and that a human ruled not
worth it — and then cannot propose the obvious remedy, because hookrelay has no
way to express "quiet **this condition** between 01:00 and 05:00". The brief is
written to say so rather than to describe a feature that is not there.

Recorded here so the gap is on file with its exact shape. Two candidate
designs, neither adopted.

## Why

What exists today, precisely:

- `POST /silences` takes `{source, minutes, note}` with `minutes` in 1..10080.
  The `silence` pipeline stage resolves it with
  `store.active_silence(ctx.source.name, now)` — **source name only**. There is
  no title, identity or field matching anywhere in the silence path, so
  silencing one noisy condition means silencing every event that arrives through
  its front door.
- The `filter` processor has the right granularity — `when` matches source,
  level, title and fields — but no schedule, and `_condition_matches` supports
  exactly three forms (exact, list membership, `{contains: …}`). No regex, no
  comparison, no time.
- Nothing in the pipeline reads a clock beyond `ctx.now` for dedup windows and
  silence expiry.

So the three honest options a patrol may propose are: silence the whole source
now for N minutes; add a permanent `filter` stage for the condition via
`PUT /config`; or a crontab line that posts the first one nightly with
`minutes` covering the window (it self-expires, so no `DELETE` is needed). The
third is a genuine recurring silence — of a source.

Two designs, both deferred:

**(a) A `schedule` on a filter stage.** `{when: {title: {contains: …}},
between: ["01:00", "05:00"], tz: "Europe/Berlin"}`. Right granularity, and it
lands in config where an operator can read it. It puts a timezone in the pipe,
which is a new class of bug — DST, container TZ against host TZ, a window that
crosses midnight — for a component whose whole value is being content-blind
plumbing. It also drops the event before `escalate-inbound`, so the
investigator loses it too, which is probably not what "stop paging me" means.

**(b) A recurring silence row.** `{source, match: {title: …}, cron: "0 1 * * *",
minutes: 240}` — silences gain a matcher and a schedule. More expressive and
strictly worse to own: silences become a scheduler with state, and the same
timezone problem arrives with a persistence layer attached.

Deferred because the cheap option is not obviously worse. Cron plus a duration
already produces a recurring silence, and its coarseness is arguably a feature:
a source-wide nightly silence is easy to reason about at 3am, where a matcher
that half-fires is not. What would change the calculus is a real deployment
where one condition in a busy source needs quieting and the rest of that source
must keep flowing — which is exactly the case the patrol will surface if it
exists.

## Consequences

- The nightly patrol is a **proposal generator with a documented ceiling**. It
  names the condition and hands a human a command; it cannot hand them the one
  they want. Its brief forbids describing a recurring condition-scoped silence,
  so the ceiling is visible in the report rather than discovered later.
- A proposal cannot become a one-click `remediation` step either, for an
  unrelated reason worth keeping together with this one: every silence command
  needs the admin token, and the remediation executor refuses `$` because it
  runs an argv rather than a shell. A literal token would end up in the
  proposal file, the case file and the card. A token-free wrapper on the volume,
  allowlisted by regex, would close that — and is a smaller change than either
  design above.
- If (a) or (b) is ever built, the nightly brief is the thing to update in the
  same change: it currently enumerates the three options by name, and a fourth
  that exists but goes unmentioned is worse than one that does not exist.
- Related: `proposed/2026-08-12-who-owns-noise-when-a-verdict-is-reused` — who
  is allowed to suppress a delivery at all is still open, and a scheduled
  silence is one more claimant to that decision.
