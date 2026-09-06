---
title: A ruling of "useless" should withdraw the runbook that run distilled
status: implemented
date: 2026-09-06
scope: hookprobe
---

## Decision

When an investigation is ruled `useless`, the runbook auto-distill wrote from
it should be withdrawn — flagged for review at minimum, removed when every case
it holds came from useless runs. Not built: the ruling and the runbook are
currently two facts about the same investigation with no wire between them.

## Why

Found by surveying the live deployment on 2026-08-31. Of 19 rated
investigations, 13 were ruled useless, and five runbooks turned out to have
been distilled from runs where EVERY case carried that ruling. They were being
loaded into every later run as "what previous investigations checked".

The clearest one was `redis-key-count-surge-net-150k-in-1h`. Its single case
records nineteen tool calls, and what they searched was the investigator's own
container: `/data`, `/data/results`, `/data/audit`, `mcp.json`,
`memory-suggestions.jsonl`, then a grep of its own case files. It never reached
Redis, because `which redis-cli` found nothing — the investigator has no
instrument for that question. The ruling said so: "Confirmed the +161k key
surge is real but could not name a driver."

The loop then taught that method forward. A later run against the same
condition was ruled useless with the words "Recycled the same 'list hygiene +
batch' hypothesis" — the learning loop amplifying a conclusion that had already
been judged worthless. auto_distill already refuses to write from a run that
FAILED, changed its inputs, or produced no report; "was later judged useless"
is the same class of fact arriving after the write, and nothing acts on it.

The seven runbooks (five all-useless, two test artefacts) were removed by hand
this session, taking the deployment from 19 to 12. Doing it by hand is the
argument for the wire, not a substitute for it.

## Consequences

- The rule wants care at the edges: a runbook with two good cases and one
  useless one should lose the case, not the runbook (`ses-bounce-spike-200-in-1h`
  is exactly that shape today). Withdrawing whole runbooks is only right when
  every case is useless.
- It gives `ruled_by` a second job. A patrol-inferred useless ruling deleting a
  runbook unattended is the model editing its own inputs by a slower route —
  the thing hookprobe.inputs exists to prevent. So an inferred ruling should
  FLAG; only a person's ruling should withdraw.
- The underlying problem is upstream of the loop and worth naming separately:
  investigations fail on resource questions (Redis, MQ depth, host CPU) because
  the investigator has no instruments for them, and succeed on configuration
  questions (Grafana rule UIDs, AWS notices) because those it can reach. That
  is a tool-access decision, not a model-quality one, and no runbook hygiene
  fixes it.

## Built (2026-09-06)

The wire is `distill.reconsider_after_useless(skills_dir, session_key, ruling_of)`,
called from `service.record_ruling` the moment a ruling of "useless" lands. It
reads each runbook's cases for the session keys `case_block` stamps in them —
the provenance that was already on disk and unused — and:

- withdraws a runbook whose EVERY case is from a useless run: snapshot to
  history, then remove SKILL.md so it stops being loaded. `ruling_of` is passed
  in, so the module stays a pure function of the files plus a lookup and the
  store never leaks into it.
- flags one that still holds a useful or unruled case: marked unreviewed with
  the reason, nothing removed, because a runbook that got four incidents right
  should not be deleted for one it got wrong.

Reversibility came with a fix it exposed: `skill_history_restore` refused to act
when SKILL.md was absent, which is exactly the state a withdrawal leaves and
exactly when a restore is wanted. It now recreates a withdrawn manifest from
history, so the removal is a true undo rather than a delete.

Tied into the graduation record shipped the same week: a withdrawal is a
`distill`-class regret, and auto_distill now records an `auto_applied` on each
INSTALL (not on updates, so a runbook is one row). Written = auto_applied,
withdrawn = regretted, and the distill class's record is honest for the first
time — an auto-writing class that keeps producing worthless runbooks accumulates
the regrets that hold it below auto_apply.

The manual cleanup that motivated this (seven runbooks removed by hand, 19->12)
stays the argument for the wire, not a thing to repeat.
