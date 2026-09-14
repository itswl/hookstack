---
title: What the door declined and what the golden gate found are numbers on the weekly page, not lines in a log
status: implemented
date: 2026-09-14
scope: stack
---

## Decision

Two gates that ran and left no legible trace now leave one where the page is
read.

The decline list (`HOOKPROBE_DECLINE_PATTERNS`) writes one line per refusal to
`declines.jsonl` under the investigator's workdir — when, which title, which
pattern — and `GET /v1/declines?hours=` counts them: events, distinct
conditions, per pattern, with `configured` saying whether a list exists at
all. The weekly page prints the count priced at the week's average paid run,
labelled as that counterfactual, and keeps three sentences apart: a node with
no list, a list that matched nothing, and a node too old to answer.

The golden gate that `scripts/deploy.sh` runs between build and up now passes
`--record /data/eval-gate.json`, and `eval.py` writes the verdict there before
acting on it — counts, votes, a timestamp, a `thin` flag for a pass on fewer
firing rows than the gate's floor; never ids or alert text, because the judge's
`/status` echoes the file as `eval_gate` and the weekly page prints it dated,
with the thin qualifier in the same breath as the green.

## Why

Both gates are 2026-09-14 gates being graded on the evidence they leave.

The decline list went live at midday and, as this is written, has declined
nothing: the one mail-family title that fired since carries no family prefix,
so the pattern written for it did not match. The only way to learn that was to
grep a container log for a line that was not there. The weekly page, whose whole
job is to say what the week cost and what was avoided, would have read the same
whether the list had saved twenty investigations or matched nothing at all —
the "recorded and unread" failure the posture-refusal line was added to fix on
2026-09-09, one step worse: not even recorded. A gate whose effect is invisible
is a gate that gets removed the next time somebody trims configuration, and a
gate that silently matches nothing is one nobody fixes.

The golden gate has run at every deploy since 2026-08-24 and its verdict has
lived in the deploying terminal. Today's deploy printed a green on nine firing
rows out of thirty-two — twenty-three are recovery rows the gate cannot fail
on — and the console qualified it as thin. Nothing else did: `/status` had no
notion the gate existed, and the page could say "the judge judged 320 verdicts
this week" without being able to say when the prompt was last measured or on
how much. The 2026-09-01 note on recoveries warned that a gate whose green is
not understood is one people learn to pass with `SKIP_EVAL=1`; a green that is
never shown is one nobody learns anything from.

**Why counts and never ids.** The record sits on the judge's data volume and is
served by a read route. Golden ids and rule keys stay in the console, where the
gate already prints them for whoever is fixing the prompt.

**Why the record is written before the verdict is acted on.** A red gate is the
verdict most worth showing; a recorder that only wrote on green would produce a
page that reads "last gate: green, 40 days ago" through a month of blocked
deploys.

**Why three-valued.** "Declined 0" from a node with no list would be read as the
list finding nothing to decline; a node that predates the route answers no
tally at all. The page already refuses to print an unread service as a zero,
and this follows that rule.

## Consequences

- One JSONL ledger per investigator workdir, a few lines a day, never pruned by
  this change; `HOOKPROBE_RETENTION_*` does not touch it. If it ever matters, it
  is a `tail` away.
- `eval-gate.json` is one file on the judge's volume, overwritten per deploy.
- The deploy that shipped this did NOT write it. `deploy.sh` pulls at line 31
  and runs the gate at line 63, and bash had already buffered the old script
  before the pull replaced it: the gate ran from the new checkout's `eval.py`
  but with the old command line, so no `--record`. A change to `deploy.sh`
  takes effect one deploy late — every time. The record was filled in by
  running the same gate line by hand on the same image (nine firing rows,
  green, thin); the next deploy overwrites it the ordinary way.
- The page grows two lines. hookjudge spends 13 of its ceiling lines on the
  status field; hookrelay is untouched.
- Follow-up the page now makes obvious rather than solves: the gate stays thin
  until the golden set gains firing rows, and the 2026-09-01 labelling packet
  is where those come from.
