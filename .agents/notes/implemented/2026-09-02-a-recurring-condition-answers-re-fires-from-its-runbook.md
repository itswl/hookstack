---
title: A recurring investigated condition answers its re-fires from its last real investigation, not by re-paying
status: implemented
date: 2026-09-02
scope: hookprobe
---

## Decision

For a condition that recurs on a cadence longer than the coalesce window, stop
funding a full cold-start investigation on every fire. A re-fire inside
`HOOKPROBE_REFIRE_ANSWER_HOURS` of the condition's last REAL investigation —
same source and title, same level, no recovery recorded since, and that
investigation completed cleanly — is answered from that investigation's report
(plus the runbook when one exists) at $0, marked `answered_from_runbook` and
`refire_of`, and still delivered as a card. A real investigation is earned again
when the window lapses, the level moves, a recovery arrives, or ten answers have
been given on one anchor. Off by default; on production it is 24 hours.

Alongside it, a person can now file a standing CONDITION ruling through
`POST /v1/rulings`, and while current it outranks the patrol's inference.

## Why

Proposed 2026-09-02 on a week where the investigator spent **$39.6** and
**$18.6 of it (47%)** went to 20 investigations of three SES bounce-rate
conditions that each returned the same finding. The existing coalescing
(`HOOKPROBE_COALESCE_WINDOW_SECONDS`, 1800s) joins re-fires of the same
`event_id`/title within 30 minutes into a follow-up turn — but these re-fire
with fresh ids, hours apart, so each is a new cold start. The idempotency key is
(source, event_id); a new id is a new run, by design.

Re-measured 2026-09-14, from the deploy host, before building: **$13.4 of the
week's $19.1 (70%)** went to SES conditions. One of them re-fired **every four
hours** through 2026-09-07/08 — 19:18, 23:23, 03:28, 07:33, 11:38, 15:43 — six
cold starts at $0.65–2.71 reaching one finding, five of them proposing the same
read-only `aws sesv2 get-account` check. So the cadence the first draft called
"daily" is four-hourly, and a daily anchor would have missed it.

**Why neither existing $0 path caught this.** Both wait for a verdict. The
ruling gate needs a standing `not_worth_it`; the vouched path
(`HOOKPROBE_RUNBOOK_ANSWER_DAYS`, off on production) needs a `useful` ruling on
the last real run. On this deployment the verdicts come from patrols on a weekly
cadence: the six SES runs of 09-07/08 were ruled `useful` by the run-rulings
patrol on **09-09**, and the ai-rulings patrol had ruled the conditions
`worth_it` on 08-20 and 08-27 — correctly, they are real, and beside the point.
"Is this real" and "should we pay to rediscover it every four hours" are
different questions, and the ruling axis answers the first. The money was spent
before any ruling existed, which is why the new gate keys on the one fact that
IS there at the moment of the re-fire: a clean real run, a few hours old.

**Why a person needed a door.** `rulings.jsonl` rows carried no author and
`standing()` was latest-wins, so even a hand-written `not_worth_it` would have
been overwritten by the next Thursday's refile, silently. There was no endpoint
to write one anyway. The door writes `ruled_by: operator:<id>`, which `standing`
prefers over an inferred row while the ruling is current; past its TTL it lapses
like any other. Local only — the judge's `ai_rulings` table is the model's
opinions under the model's name, and a person's does not belong there under one.

## Built (2026-09-14)

`service._refire_answer`, third in the chain after the ruling gate and the
vouched path, every clause a reason to pay instead:

- **off** unless `refire_answer_hours` > 0 — answering from a report is a claim
  about the world and the default makes none;
- **never** for a patrol, a consolidation, a `task` or `brief`, or a run a
  person opened from chat (`asked_by`, `thread_root`);
- the **anchor** is the newest REAL run of the same (source, title) and must be
  a clean completed one inside the window. A runbook answer never anchors — the
  same rule `_vouching_run` states, or the chain would cite itself past the
  window. A newer real run that FAILED forfeits the anchor;
- **same level**, or it is a different question;
- **no recovery since the anchor**, checked on every run since and not the
  anchor alone, because `record_recovery` annotates the newest run of the
  condition and after the first answer that is an answer;
- **at most `_REFIRE_ANSWER_MAX` (10) answers per anchor**, fixed like the
  recovery window: enough for six re-fires a day, not a number anyone tunes.

The reply is report-shaped JSON in the same dialect as the two gates beside it,
carrying `standing_finding` (the anchor's report, 3000 chars), the runbook when
distilled, `refire_of`, `refires_since_anchor` and how to force a real run.
`meta.refire_of` names the anchor on the run record. `cost_report.py` already
counts `answered_from_runbook` as the counterfactual, so the saving is legible
on the weekly page without a change there.

`POST /v1/rulings` takes `{title, verdict, why, by}` under the console bearer
(the agent's is refused on every non-GET), files through
`rulings.file_operator_ruling`, and answers with what the ruling DOES — a
`not_worth_it` with no runbook "stands and gates nothing until one is
distilled", so a verdict that gates nothing yet does not read as if it did.

Deliberately not built:

- **A content delta check.** The first draft asked for "a one-line current-value
  check". The investigator holds no credentials for the systems it reports on
  and must not open one to freshen an answer — the same constraint the
  freshness cursor row states. The delta signals used are the ones that arrive
  through the pipe: level, recovery, and time.
- **Silence.** Every answer is still a card. The pipe's noise question
  ([[who-owns-noise-when-a-verdict-is-reused]]) is untouched: this bends the
  cost curve, not the attention one. Six SES cards a day become six $0 cards.
- **Turning the vouched path on.** Its code accepts a patrol-inferred `useful`
  as a vouch; whether an inference may license a $0 answer is a decision the
  distill-withdrawal note settled the other way for withdrawals, and it should
  be decided on its own, not switched on to ride this change.

## Consequences

- The biggest single spend line drops without a model change: on the measured
  week, five of the six four-hourly SES runs would have been answers, and the
  cost curve bends the way the README promises.
- The risk is answering "same as four hours ago" when this time is different.
  The bounds above are the answer, plus the standing one: the card says what it
  did and `force` reopens a real run. Read the first week's `refire_of` rows
  back against their anchors before trusting the number.
- Two doors still escalate the same rule on production — the relay's
  `verdict-to-me → to-probe` and the platform's deep-analysis leg — measured
  at 35 + 19 runs of one condition in five days. A refire answer keys on
  (source, title), so the two legs anchor each other only where the derived
  `source` matches the stated one. That duplication is a configuration decision
  and is recorded in the production cost-shape reading, not fixed here.
- The runbook shelf is at its cap (20/20, 58 of 85 runs skipped distill that
  week), so the highest-volume condition has no runbook. This path answers from
  the report and does not need one; the shelf is its own note.
