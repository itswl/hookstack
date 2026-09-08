---
title: The board is the overview — no ninth page, and no cross-node aggregator yet
status: proposed
date: 2026-09-08
scope: hookprobe
---

## Decision

The PRD asks for an `Overview` as the first item of a nine-item navigation,
answering six questions. Five of the six were already on the work board. The two
that had no home anywhere — **which agent this is and whether it is healthy**,
and **the failure rate** — are now on it too. There is no separate Overview
page, and no page anywhere aggregates several nodes.

## Why not a ninth page

A page repeating five numbers that already exist is a second place for them to
be wrong in, and the two boards this repository has already learned that lesson
on — the judge's review strip that moved to a tab, the approvals badge that
counted 164 rulings as debts — were both about a number saying something its
reader could not act on. The cheapest correct Overview is the board answering
the questions, which is what an operator opens anyway.

What was genuinely missing is now there: `/v1/agent` had existed for a day and
**no page read it**, so a deployment running a planner, a watcher and a work
runner told them apart by the port in the address bar; and cost had a chip while
failure rate had nothing. The rate is counted over WORK, not runs — a run that
failed and was retried into an answer is not a piece of work that failed, and
abandoned work is, because nobody came.

## Why no cross-node aggregator

The obvious home is the pipe's board: it already links to each node's console
(`HOOKRELAY_UI_LINKS`) and it is the one service every deployment has exactly
one of. Two things stop it.

**Doctrine.** The pipe's four jobs are receive, route, deliver, account. Calling
out to probes and interpreting their work boards is a fifth, and the field it
would have to read is not one it copies — it is one it would have to understand.
The pipe carries `session`, `sender` and `work_id` today precisely because it
never reads them.

**Weight.** The ceiling is 5,500 and the pipe stands at 5,421. An aggregator,
its peer configuration and a page section do not fit in 79 lines, and raising
the ceiling to add a job the doctrine argues against is the wrong order to make
that decision in.

The browser cannot do it either: the probes sit on their own ports, so a page
served by one of them fetching another is cross-origin, and opening CORS on a
console that holds a bearer token is a worse trade than not having the page.

**What it would actually take**, when somebody wants it: a peer list per node
(`HOOKPROBE_PEERS=name=url,…`), a read-only proxy on the console that fans out
`/v1/work` and `/v1/agent` with per-peer tokens, and a decision about what
happens when a peer is unreachable — which is the interesting part, because an
overview that silently omits a node it could not reach is worse than no
overview. None of that is worth building for the deployment that has one probe,
which is the one in production.

## Consequences

**The work board is now the product's front page**, and its header line is
load-bearing: six figures, each of which an operator can act on. If a seventh
turns up, it belongs there or it belongs nowhere.

**A three-probe deployment still has three boards.** On the laptop that runs a
planner, a watcher and a work runner, "what is happening" means opening three
tabs. That is honest for now and the note above says what would fix it.

## Parked on 2026-09-09, and the correction that came with it

The operator parked this until it hurts. Recorded here rather than in a second
note, because a decision with two homes is the thing this note is about.

**The correction:** everything above argues from the pipe's doctrine, its
remaining weight and CORS. Those are all true and none of them is the real
reason. The real reason is that **this stack runs unattended**. A page nobody
opens has no value however cheaply it is built, and the three-tab complaint the
note ends on is not the harm. The harm is a node blocking while the board that
would say so is one nobody opens. That is not hypothetical: it is exactly what
produced the `abandoned` state, when twelve items sat between four and
twenty-two days on a console that was up and healthy the whole time.

So the useful form of a cross-node overview is **not a view. It is a signal.**
Not "I can see every node", but "the one node that needs me finds me."

**Which means most of it already exists**: `scripts/cost_report.py` renders,
`scripts/post_watch_signal.py` delivers through a door of the pipe so the
message is accounted for like everything else, and `patrol-timer.sh` or a
crontab supplies the clock. `hookprobe/examples/patrols/README.md` already
carries the cron line for the weekly page. The single missing piece is that
`--probe` takes one node.

**What to build, when it is time:** `--probe` accepts a list, the report grows a
per-node section, and a node that could not be read is printed as unreachable
rather than omitted. No new service, nothing added to the pipe, no CORS, and the
peer tokens stay in the operator's own shell instead of being distributed to
every node so each can read the others.

**What NOT to build**, so the cheap wrong version does not get built by whoever
picks this up: not a ninth page, not an aggregator inside the pipe, and not
probe-to-probe peering, which spends N-squared credentials to reach a place a
single script already stands in.

## What counts as the pain point

Named now, while it is cheap to be honest about, because "when it hurts" decays
into "never" without a recognisable trigger:

1. **A node's work goes `abandoned` and nobody saw it happen.** Sharpest of the
   three, and the only one that is measurable: `abandoned` climbing on any node
   whose board is not the one routinely opened. This is the original harm.
2. **A second deployment that matters.** Today there is one probe in production
   and three on a laptop; the laptop's are told apart by role and are usually
   opened deliberately.
3. **"What is happening" stops having a one-tab answer.** If the honest reply
   needs more than one board, the board has stopped being the front page and
   this note's central claim has expired.
