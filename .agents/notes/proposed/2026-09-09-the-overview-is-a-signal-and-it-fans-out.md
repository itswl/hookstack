---
title: The cross-node overview, built as the signal it was parked as — one flag, and an unreachable node that says so
status: proposed
date: 2026-09-09
scope: stack
---

## Decision

Built, in exactly the form the parking note specified and no larger.
`scripts/cost_report.py --probe` is repeatable (and splits a comma-separated
list, so a crontab's `HOOKPROBE_URL` can carry several). The first node stays
the primary and drives every existing section, so a one-probe deployment's page
is unchanged character for character. Give more than one and the page grows a
`## The nodes` section: one line per node, leading with how much is waiting on a
person and how much was abandoned, and **a node that could not be read is
printed as unread rather than omitted**.

Nothing was added to the pipe, no page was added to any console, no CORS was
opened, and no node learned about another. The peer URLs live in the operator's
shell, where the tokens already are.

## Why

**The trigger fired, and it was measurable, which is why it was named.** The
parking note said this was worth building when `abandoned` climbs on a node
whose board is not the one routinely opened, or when "what is happening" stops
having a one-tab answer. Read off the running work deployment on 2026-09-09:

| node | board |
| --- | --- |
| planner | 18 done, 2 items open on a person |
| watcher | 64 done, **1 abandoned**, 1 verified |
| work runner | 2 done, 1 verified |

Three boards, three ports, three tabs, and the abandoned item is on the node
nobody opens — the watcher runs on a timer and reports into chat, so its console
is the one with no reason to be visited. Both named triggers, present at once.

**It is a signal, not a view, and that is the whole shape.** The PRD asks for an
`Overview` as the first of nine pages. On an unattended deployment a page has no
value at any price, because nobody opens it; the harm was never "I cannot see
every node", it was "a node needed somebody and the thing that would have said
so was a page nobody opens". The fix therefore has to arrive somewhere a person
already is. This page is rendered by a clock and posted through a door of the
pipe, which means it is delivered, accounted for, retried and dead-lettered like
every other message in the stack — and it already carried the week's work
figures, so the per-node lines ride a delivery that exists.

**An unreachable node is the only interesting decision in the feature.** A node
that could not be read has an unknown board, not an empty one. An overview that
drops it prints "nothing is blocked" on evidence it does not have, which is the
same failure as the healthy console with twelve abandoned items on it — faster,
and with more confidence behind it. So the two doors are tracked apart: a node
whose `/v1/agent` answered and whose `/v1/work` timed out gets a line saying its
work is unknown, rather than a row of zeros. This is the case the parking note
called the interesting part, and it is the one the tests are pointed at.

**With one node the section does not exist.** Not an empty section, not a
heading with one row — absent. The board is still the front page for a
single-node deployment, and repeating its six figures under a new heading would
be the second place to be wrong that this report already refuses in three other
sections.

**The row says name, runtime and posture, and not the role.** A role is a
sentence written for one node's own console ("turns a work signal into a plan a
person can approve; hands …"), and cutting it to fit a row cuts it mid-clause,
which reads as a worse version of saying nothing. It stays in `--json`. The
posture earns its place because exactly one node on this deployment may write.
The failure rate is deliberately NOT on the row: it is
`(needs_human + abandoned) / items`, so the two figures already there carry it,
and two numbers that can contradict each other are worse than one.

## Consequences

**The PRD's `Overview` is answered and will still not be a page.** Two notes now
argue that, and this one supplies the mechanism the second one specified. If
somebody wants the page later, the argument to beat is that nobody opens it.

**One shared token per report run.** All three nodes of a deployment take the
same `HOOKPROBE_TOKEN` from the same `.env`, so the fan-out uses one. A
deployment that gives its nodes separate tokens cannot use this yet, and the
honest fix then is per-node token env, not distributing every token to every
node.

**The crontab line is now under-specified on a multi-node deployment.** The one
in `hookprobe/examples/patrols/README.md` names a single probe; it has been
updated, but any crontab already installed from it keeps reporting one node
until an operator edits it. Nothing warns them, because nothing can: a report
asked for one node correctly reports one node.

**Still no probe-to-probe peering, and it should stay that way.** It spends
N-squared credentials to reach a place one script already stands in.
