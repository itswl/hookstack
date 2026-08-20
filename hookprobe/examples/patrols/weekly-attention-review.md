This is a scheduled patrol, not an alert. There is no incident and no root
cause to find. Do the task below and ignore the framing that asks you to
triage something.

QUESTION: is the noise going up or down?

1. Read the judge's ledger over seven days:
   curl -sS "http://hookjudge:8200/status?window_hours=168" -H "X-Read-Token: $HOOKJUDGE_READ_TOKEN"
   (drop the header if no read token is configured — /status stays open then).
   The block you want is summary.attention: interruptions, conditions,
   repeats, mattered, did_not_matter, ruled, mattered_pct, and noisiest[]
   with identity, title, interruptions, paid, mattered, did_not_matter.

2. Find last week's edition of this patrol BEFORE concluding anything: Grep
   /data/results/ for this task's title and read the most recent hit. If
   there is none, say so plainly — this run is the baseline, and a baseline
   is a finding, not a failure.

3. Lead with the direction. The number that answers the question is
   interruptions / conditions — cards per condition. Give last week's, this
   week's, the direction, and the size of the change. `repeats` is the same
   fact from the other side: cards that restated a condition the operator had
   already been told about.

4. Then the spend. summary.cost and summary.paid_ratio_pct are the bill;
   attention is what it bought. Per entry in noisiest[], `paid` against
   `interruptions` is the line worth quoting: twelve interruptions with one
   paid means eleven free cards that still spent somebody's attention.

WHAT YOU MUST NOT DO WITH MISSING DATA

`mattered_pct` is null whenever `ruled` is 0, and `ruled` counts human button
presses. A press exists only on channels that render interactive callbacks;
on the others a human cannot rule at all, however much a card annoyed them.

- Never write or imply that an unruled interruption did not matter. Silence
  here is absence of evidence, not evidence of absence.
- If ruled is 0, say the rulings are unavailable, name the reason you can
  actually check (which channels carry callbacks), and answer the volume
  question anyway — cards per condition needs no rulings.
- If ruled is small, give counts ("3 of 214 ruled"), not the percentage
  alone. A percentage over a handful is not a trend.
- noisiest[] is capped at the five loudest conditions that interrupted more
  than once. Do not present it as the whole list and do not sum it into a
  total — the totals are the fields beside it.
- summary.* is windowed by window_hours; the `recent` array is not, it is
  simply the newest rows. Do not read it as "this week".

Count your own footprint. This patrol enters through the same front door as
an alert, so unless the pipe routes it around the judge it is one of the
interruptions you are counting, and its repeated title makes it a `repeat`.
Say how many rows are patrols.

OUTPUT

A short Markdown report, conclusion first. The opening sentence is what the
notification card quotes, so it carries the direction and the number — e.g.
"Noise is down: 4.1 cards per condition this week against 6.8 last week."
Then the comparison, then the spend, then what you could not measure and why.

No remediation block. Proposing that something be turned off is the silence
patrol's job, not this one's.
