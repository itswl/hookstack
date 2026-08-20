---
title: Trend reporting and silence proposals are patrols in hookprobe, not features in hookjudge
status: implemented
date: 2026-08-20
scope: stack
---

## Decision

"Is the noise going up or down" and "propose silencing this condition" ship as
**prompts** — two task briefs and two crontab lines under
`hookprobe/examples/patrols/`, delivered through the front door the same way an
alert is. Neither service gained a line of code. In particular hookjudge grew
no report renderer, no week-over-week comparison, no time-series storage and no
scheduler; hookrelay grew no schedule field.

The judge's job stops at `summary.attention` and `GET /status?window_hours=N`:
it publishes the numbers for a window and nothing more. Reading two windows,
remembering the last one and writing the sentence a human acts on is an
investigator's run.

## Why

Both capabilities are *readers* of data that already exists, and every piece of
machinery they need was already built for another reason:

- **The window is a query parameter.** `/status?window_hours=168` already
  returns seven days of `summary.attention`; the weekly review needed no new
  aggregation. Building a "weekly report" in hookjudge would have meant a
  second code path computing what the first one already computes.
- **The memory is a case file.** Comparing this week against last week needs
  last week's answer stored somewhere. hookprobe keeps the full record of every
  run on the volume and its briefs already tell the agent to open older records
  of the same task first — verified by the second patrol ever run, which opened
  the first patrol's case file and compared dimension by dimension. A
  time-series table in the judge would be a third copy of history beside the
  ledger and the case files.
- **The schedule is cron.** Already the mechanism for
  `scripts/backup_probe_data.sh` and for patrol mode itself. A scheduler inside
  a service that is deliberately the smallest brain in the family is the wrong
  place for it.
- **The delivery is the existing return leg.** The report reaches the same
  channels through `probe-notify`, dressed by the pipe. A reporting layer in
  hookjudge would have had to learn a channel format, which is exactly the
  thing the family's split exists to prevent — the reason the judge does not
  render cards is the reason it should not render reports.

The cost of getting this wrong is specific: hookjudge is the component whose
value is being *the smallest brain that can hold up its end*, so that it can be
replaced or compared while both edges stay still. A report renderer and a
trend store are the two things most likely to make it un-replaceable, and
neither would be paid for by a single verdict.

There is also an honesty argument. A trend feature computing `mattered_pct`
would publish a number that is null until humans press buttons, and on channels
without interactive callbacks they cannot press any. A prose report can say
"nobody could rule these, so read the volume figure and not the percentage" —
a chart cannot, and the briefs are written to require that caveat rather than
allow it.

## Consequences

- **The prompt is the product, so it is reviewed like one.** The briefs live in
  `hookprobe/examples/patrols/` as examples and are meant to be copied outside
  the checkout and edited there; a `git pull` must not silently rewrite an
  operator's prompt. Their quality is the deliverable, and a bad edit degrades
  a report rather than crashing a service — which is harder to notice.
- **Each patrol costs a paid agent run.** Eight runs a week (one weekly, seven
  nightly) is real money and lands under `HOOKPROBE_BUDGET_USD` like any
  autonomous spend. A patrol that is refused by the breaker still sends a card
  saying so, which is the correct failure.
- **A patrol measures a ledger it writes to.** Every front-door event also
  reaches the judge, so an un-routed patrol becomes a verdict, a card and a
  `repeat` in the very attention block it reports on. The recommended
  `patrol-in` route (priority above `escalate-inbound`, `stop: true`) keeps it
  out of the judge while still funding the investigation; the weekly brief
  tells the agent to count its own footprint in case the route is absent.
- **Briefs are capped at 4000 bytes** by the event door's body cap. Above it
  the last instruction silently never reaches the model, so `patrol.sh` refuses
  instead of sending. A brief that outgrows the cap is the signal to move
  method into a SKILL.md on the volume and leave the task in the brief.
- **The silence proposal is bounded by what hookrelay can express**, not by the
  prompt: silences match a source, not a condition, and nothing anywhere takes
  a recurring schedule. Recorded separately as
  `proposed/2026-08-20-a-recurring-condition-scoped-silence`.
- **Revisit if the report becomes a dashboard.** The moment somebody wants the
  trend on a page rather than in a card, this stops being an investigation and
  becomes a query — and the right home for that is the judge's status page or a
  Prometheus dashboard over `hookjudge_condition_interruptions`, not a longer
  prompt.
