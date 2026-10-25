---
title: The promises a review found unkept — seven fixes, and the three behaviour changes that ride with them
status: implemented
date: 2026-10-25
scope: stack
---

## Decision

A read-only review of the three services found seven places where a documented
promise was not held by the running path. Each is fixed here, with a regression
test at the point the promise is consumed rather than at the point it is
computed.

1. **A live alert is no longer read as its own recovery.** The edge marker
   required a marker, not merely a word at the start of a title; English
   recovery words count only at an edge, after a separator, or with a
   time/agent; `not OK` and `status != OK` are refused by the word before them.
   A recovery also inherits its firing's verdict with no reuse-window bound, and
   the review queue no longer asks a person to re-rate a verdict the judge
   inherited. (`hookjudge/contract.py`, `store.py`, `app.py`.)
2. **A remediation line joins the audit chain.** `remediation._audit` calls
   `audit.append` instead of appending a raw line, so the first approved
   procedure no longer makes the hourly selftest report CHAIN BREAKS.
3. **A routed event, its decision and its deliveries are one transaction.** The
   front door and card actions call `Store.record_event`; a failure in any part
   rolls the unit back. The abstraction existed and the call sites ignored it.
4. **Case files are evidence, not scratch.** `results/` joined the input guard's
   protected paths; the service still writes them, the agent reads them, and a
   run can no longer forge or delete what the next run treats as prior evidence.
5. **Config values and the config surface say what they mean.** `${VAR}`
   resolves inside a value (the documented `Bearer ${BRAIN_KEY}` header no
   longer sends its own name), `on_error` accepts only `pass`/`drop` at load,
   a block-scalar `secret:` is masked with its body, and a card silence naming a
   source that no longer exists is refused instead of widened to `*`.
6. **The running work deployment is covered by the backup and restore scripts,
   and the family compose passes the judge's four door secrets.** `work-data`
   was absent from `backup_probe_data.sh`, and `deploy/docker-compose.yml` left
   `/events` unsigned and `/rulings/ai` at 503.
7. **A 202 from the judge is a promise with a row behind it.** `/events`
   synchronously inserts the accepted event into an `intake` table before
   answering; the background task drops it only once a judgement exists, and
   boot replays what is left. A kill between the answer and the judgement used
   to take the alert out of the ledger, the dead letters and `/status` at once.
   (`hookjudge/store.py`, `app.py`.)

The pipe and judge ceilings move to 6,550 and 3,540 because these fixes add
lines to two services that were already inside their budget; the raise is
argued in `scripts/assert_weight.py` and stated in both READMEs.

## Why

The recurring bug shape in this repository is a value computed correctly and
dropped before it acts. This batch is that shape in its most expensive form:
the judge could green-card a live incident as resolved, the audit of the one
path that executes commands broke the audit chain, a crash could leave a routed
event with fewer deliveries than the ledger claimed, and the deployment that is
actually running had no backup script covering its data. Each was verified
against the source (and the recovery classifier against its regex) before it
was changed; each fix has a test that fails on the old behaviour.

Two of the fixes are security posture changes, not bug fixes: `results/` is now
read-only to the agent, and an unknown source in a card silence is a 409. Both
remove a path where an injected run or a stale card could act on more than it
was given.

## Consequences

- **Operators of the family compose must set `HOOKJUDGE_INGEST_SECRET` and
  `HOOKJUDGE_RETURN_SECRET` in `.env`.** The compose now refuses to start
  without them; that is the point. A deployment that relied on unsigned judge
  doors will fail loudly rather than accept anyone on the network.
- **The agent can no longer write `/data/results`.** Scratch work belongs in
  `/data/scratch` (or `/tmp`); the case files are service-written.
- **A card silence for a source removed from config answers 409.** The token is
  already spent at that point, so the press is refused once and a retry reads
  `already_done`; nothing is silenced.
- The judge's review queue is smaller by exactly the inherited recovery rows;
  the agreement matrix already excluded them, so the two views now agree.
- Backups taken by `backup_probe_data.sh` include `work-data`; restore prints
  the `hookstack-work` compose commands. The script still does not touch a
  running container, by design.
- An accepted event now has a row before the 202, so the pipe's retry does not
  need to cover a process kill. A replay after a kill may judge the same event
  twice; the identity route makes the second verdict free, and a duplicate card
  is cheaper than the alert disappearing.
