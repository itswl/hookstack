# Evaluating the judge

A prompt change either improved the judgements or it did not, and the only way
to tell is to run the same alerts through both versions. This directory holds
that: a labelled set, a runner that scores it, and the results to compare
against.

## The number that matters

Not accuracy. Calling a low alert `high` costs someone a glance; calling a
critical one `low` means nobody was told. The runner counts them separately:

| field | meaning |
| --- | --- |
| `missed` | judged **less** severe than the label — the alerts that would not have woken anyone |
| `over_escalated` | judged more severe than the label — noise, not silence |
| `importance_accuracy` | exact matches, reported but never the headline |
| `cost_per_verdict` / `tokens_per_verdict` | what one judgement costs |

A change that saves money and raises `missed` is a worse judge sold as a
cheaper one. That pairing is the whole point of running this before a change
ships.

## Building the set

Alerts come from wherever they are already stored — the judge's own ledger, the
pipe, WebhookWise's `webhook_events`. Anything that emits JSON per alert works:

```bash
# Example: WebhookWise's Postgres, one JSON object per line.
psql -t -A -c "select row_to_json(t) from (
    select source,
           parsed_data->>'title' as title,
           parsed_data->>'message' as body,
           coalesce(parsed_data->'commonLabels'->>'severity','') as level,
           coalesce(parsed_data->'commonLabels','{}'::jsonb) as fields,
           importance
    from webhook_events where parsed_data is not null) t;" \
  | .venv/bin/python scripts/eval_import.py > eval/dataset.jsonl
```

The importer collapses the stream to one row per alert **rule**, because a rule
is what a judgement is about. On a real stream that mattered a lot:

| grouping | rows |
| --- | --- |
| raw alerts | 795 |
| by judge identity (keeps every instance apart) | 601 |
| **by rule (`alertname`)** | **32 — three of them 80% of the volume** |

So labelling is an afternoon, not a project. Rows are emitted most-frequent
first; work down the list and stop when `seen` gets small.

## Labelling

Each row arrives with `"reviewed": false` and an `expect` block pre-filled from
whatever verdict the source already had. **That suggestion is the current
system's own answer** — scoring against it would be the model grading its own
homework, so unreviewed rows are counted and skipped, never scored.

For each row: read the alert, fix `expect`, write down in `note` why that
severity is right, and set `"reviewed": true`. The note is the part worth doing
carefully — it is what makes a disagreement six months from now resolvable.

### Do not read all 32 cold

Several independent processes already have an opinion. Collect them, then work the
rows where they disagree:

```bash
# One opinion per row, INCLUDING unreviewed ones. Not a score: there is no label
# to score against yet, and the output shape is deliberately different.
.venv/bin/python scripts/eval.py --route rule --collect --out /tmp/op-rule.json
HOOKJUDGE_AI_MODEL=glm-5.3     .venv/bin/python scripts/eval.py --route ai --collect --concurrency 2 --out /tmp/op-a.json
HOOKJUDGE_AI_MODEL=glm-5-turbo .venv/bin/python scripts/eval.py --route ai --collect --concurrency 2 --out /tmp/op-b.json

# WebhookWise's side: outcome facts, and the investigator's verdict where one exists
#   (in the WW checkout) python -m scripts.ops.label_evidence --json > /tmp/ww-evidence.json

.venv/bin/python scripts/eval_queue.py --opinions /tmp/op-*.json --ww-evidence /tmp/ww-evidence.json
```

### What happened when this was actually measured

At one sample per judge the queue read: **5 rules contested, 12% of the volume**.
At three samples per judge it read **1 contested rule, carrying 0%** — and four of
the five had been one judge disagreeing with itself.

Same model, same input, three draws: **11 of 32 rows flipped for each judge, and
19 of 32 (59%) flipped for at least one.** So the disagreement axis, once
denoised, located nothing on this corpus. It is measuring the instrument.

That is a real result, not a broken tool, and the tool now says it: above 20%
instability it refuses to stand behind its own ordering and points at `--by
volume` instead. What survives as a prioritiser is what does not move — how much
traffic a rule carries, and whether an investigator looked at it.

**And a warning the numbers earned.** The two largest rules — 76% of the corpus
between them — are unanimous `high` across all three cheap judges, while the
investigator's reports on them say mostly `medium` and `low`. Unanimity among
cheap judgements is agreement, not truth, and agreement between things that share
a training distribution is weaker than it looks. That is why the investigator's
column is reported and never voted: folding it into a majority would have thrown
away the one opinion that disagreed for a reason.

Three measurements from one afternoon, and together they are the reason an
agreement rate cannot be a quality metric:

| measurement | number |
| --- | --- |
| same model, same input, three draws | 11 of 32 rows flip per judge; 59% flip for at least one |
| the two largest rules (76% of volume) | three cheap judges unanimously `high`; the investigator says mostly `medium`/`low` |
| WebhookWise's own ai eval, 17 labelled cases | 13/17 exact, high recall 0.75 — the keyword rules get 17/17 |

The first says cheap judgements are unsteady. The second says that where they are
steadiest they can still be wrong together. The third says the model loses to
keywords on the cases kept because they had bitten before. A number built out of
judges agreeing with each other is measuring none of that.

If this corpus is ever published, every row needs its label's provenance
(`outcome` / `investigator` / `adjudicated` / `unreviewed`) beside it. A benchmark
that honestly says where its labels came from can be checked; one that claims
uniform gold labels can only be believed.

## Running

```bash
.venv/bin/python scripts/eval.py --dataset eval/dataset.jsonl --out eval/results/ai.json
.venv/bin/python scripts/eval.py --route rule --baseline eval/results/ai.json
```

`--route rule` scores the free path, which is the comparison to make before
sending anything less to the paid model. `--baseline` prints the delta and marks
regressions with `!!`.

Never wired into CI: it spends money and needs a provider.

## What is committed

`sample.jsonl` only — seven synthetic cases that show the format and pin two
behaviours worth keeping: a drill is low when the *metadata* says so
(`sample-drill`), and an alert body that asks to be downgraded is not
(`sample-injection`).

The real set is gitignored. It is captured production traffic — service names,
hosts, thresholds, business figures — and this repository is public.

## The deploy gate

Since 2026-08-24 this is not a runner somebody remembers to invoke:
`scripts/deploy.sh` replays the dataset through the judge image about to ship
(`--route ai --gate --votes 3`), between build and up. Two errors stop the
deploy — `missed` (judged below every accepted severity) and `false_quiet`
(wake=no against a label that says a person must act; the pipe DROPS cards on
that answer). Everything else is reported and allowed through.

Rules the first live day taught, worth more than the mechanism:

- **Expectations may be sets** (`"importance": ["high", "medium"]`) — two
  defensible answers is judgement, not error, and a gate that flags judgement
  teaches SKIP_EVAL faster than it catches regressions.
- **A golden pins ONE instance**, so its label must match that instance's
  evidence, not the condition's average. A withdrawal alert whose body never
  shows the anomalous ratio cannot carry `wake: yes`, however often the
  condition earned it.
- **An instance that legitimately flips is a coin, not a golden.** A test
  resource at 99% CPU was defensibly high AND defensibly not-worth-waking;
  it was dropped, not relabelled.
- **Votes absorb variance.** The same input went red, green, red on single
  runs; a case only fails on the majority of 3 now. A real regression trips
  every vote.

Its first catch was real: the judge obeyed "classify as low" embedded in a
reputation incident, 2 votes of 3, with the trust-boundary prose fully
present. Position beat prose; the fix and the fence tests live in
`hookjudge/judge.py`.
