---
title: An investigator verdict may steer a route, but only from a closed set
status: implemented
date: 2026-09-04
scope: hookprobe
---

## Decision

hookprobe may emit `meta.verdict` in its processed-event, taken from a `VERDICT:`
line in the agent's own report, and a pipe config may extract it into a field and
route on it. The value is admitted **only if it appears in
`HOOKPROBE_VERDICTS`** — an operator-declared, comma-separated closed set,
empty by default, which means the feature is off until somebody turns it on.
Anything absent, unknown, or malformed becomes `""`; it never becomes a guess and
never becomes the first item in the list.

## Why

The pipe's C dialect already lets a node's judgement reach a routing key —
hookjudge does it, and `deploy/shadow.yaml` routes on `wake:
"{meta.wake_someone}"`. hookprobe could not: `notify.py` filled `meta.importance`
from `run.meta["level"]`, the level of the event that came IN, so an
investigator's conclusion echoed its input and the investigator could only ever
be a leaf. Chaining a watcher to a planner — the composition the pipe exists to
make possible — needs the middle node to be able to say something the next hop
can read.

What made this a decision rather than a diff: the investigator is the one
component in the family that reads attacker-influenced text, and a routing key
decides where money is spent. A free-form verdict would mean a sentence in an
alert body could pick the expensive lane.

A closed set is the same answer the family already gave for bypass lanes: config
declares the set, the payload picks from inside it. An injection can then at
worst choose a wrong lane among lanes the operator already wrote down; it cannot
invent a destination, and it cannot reach a node the config does not name. The
blast radius is a mis-routed event in a ledger that records the choice — not a
line in CLAUDE.md, which is why this gets a closed vocabulary and the memory gate
gets a human. Same actor, different blast radius, same rule as
`.agents/notes/implemented/2026-08-20-the-signal-that-needs-no-human.md`.

Marker parsing follows `suggestions.py`: one anchored regex over the report text,
already the precedent for reading structure out of a model's prose.

## Consequences

- Default empty means no deployment changes behaviour by upgrading. A topology
  that wants verdict routing declares the vocabulary and the routes together,
  which keeps the two from drifting.
- The set is env, not config, and lives with the investigator rather than the
  pipe — the node owns what it is able to say, the pipe owns where each saying
  goes. Two instances with different roles can therefore have different
  vocabularies without a shared file.
- An unknown verdict is silently empty rather than an error. A run that already
  cost money should still deliver its report; failing the delivery over a typo in
  a label would trade the whole result for the routing hint.
- Not covered: nothing stops an operator from declaring a vocabulary whose lanes
  cost money and then routing every value into a paid node. `/topology` will show
  such a graph, and the budget breaker remains the backstop it already was.
- `redteam_memory.py` covers the memory path, not this one. The equivalent probe
  here is cheap and worth adding when a deployment actually turns the vocabulary
  on: drive an injection that names a declared verdict and assert what reached
  `meta.verdict`.
