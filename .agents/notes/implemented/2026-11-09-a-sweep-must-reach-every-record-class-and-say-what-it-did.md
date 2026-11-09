---
title: A sweep must reach every record class, and say what it did
status: implemented
date: 2026-11-09
scope: stack
---

## Decision

Three sweeps, one question asked of each: which records does it never reach,
and does it say what it did.

- The pipe's retention purge deletes the card presses of the events it purges.
  A press stays while its event does, and while it is younger than the window.
- The work deployment's watcher sweeps case files and transcripts older than
  thirty days (`WATCH_RETENTION_DAYS`; 0 keeps everything). The planner and the
  work runner keep the investigator's default, which keeps everything.
- Every sweep says what it did: the judge's purge logs its count like the
  pipe's, and the investigator's two sweeps count the files they could not
  remove or inspect and warn each time, instead of skipping them in silence.

## Why

A second deployment running copies of these files upgraded its runtime, and
the new version began closing orphaned sessions at startup under a new end
reason. Its daily cleanup matched only sessions that had never ended, so it
deleted nothing for six days and said nothing; a weekly check found six hundred
of them. Asked of this repository, the same question found two answers.

`card_actions` was the one table in the pipe's ledger no sweep touched. The
purge comment promises a ledger that does not grow forever once all traffic
rides through it, the signature tables bound themselves to their windows, and
a press has no foreign key to its event, so the presses of purged events stayed
behind. Small, one row per press, and exactly the shape found elsewhere.

The investigator keeps case files forever by default on purpose: they are the
agent's recall. The watcher's are not investigations but rounds, and by the
stop on 2026-10-09 five weeks had left 356 case files, 317 telemetry files and
36 audit days on its volume with nothing removing them. Recall there is the
model grepping the results directory, so the cost of growth is time per round,
not prompt size. Thirty days is far beyond the signer's hour-long settlement
and the next round's grep. The audit chain is verified over its last seven
day files and its first line in the window may point outside it, so a swept
day is never a break.

A sweep that cannot remove a file skipped it in silence and still read as
healthy. The pipe logged only when it removed something, the judge never.

## Consequences

- The purge's return gains `presses`, and the pipe logs the three counts when
  any is non-zero.
- On the watch node, a round's case file, telemetry and audit day are gone
  after thirty days; a follow-up on a round older than that finds no
  transcript and is reported as such, the way the configuration doc says.
- A file the investigator's user cannot delete is a warning on every daily
  sweep until someone removes it or fixes its ownership.
- Not done: a per-table row count on the console, and an integrity check of
  the ledgers after an abrupt stop. The doctor script knows no ledger paths,
  and the deployment that measured such a stop found its ledger intact.
