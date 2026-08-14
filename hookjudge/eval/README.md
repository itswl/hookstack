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
