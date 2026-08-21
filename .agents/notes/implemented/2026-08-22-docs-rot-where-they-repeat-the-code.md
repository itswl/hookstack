---
title: Docs rot where they repeat the code — check what is enumerable, stop duplicating what is not
status: implemented
date: 2026-08-22
scope: stack
---

## Decision

Two halves, because doc drift is two different problems wearing one name.

**Enumerable facts get a check.** `scripts/assert_docs.py` asserts that every
route and every environment variable in the three services either appears in a
document or is named in a table with the reason it does not, and that a document
restating an enumerated set (`actions.KINDS`, `rulings.VERDICTS`) lists all of it.
In the gate, in ci-stack, pinned by the same contract test as its five siblings.

**Prose about behaviour does not get a check, because none is possible.** It gets
a rule instead: a README describes INTENT, and MECHANISM belongs in the docstring
beside the code.

## Why

An audit on 2026-08-22 found four statements that had become false. Three were
prose:

    "the draft lands as proposal.md and waits for review"   consolidation applies itself
    "memory suggestions have no switch"                     HOOKPROBE_MEMORY_AUTO_APPLY
    "restore is just the PUT"                               there is a restore endpoint

and one was enumerable:

    "Five kinds exist: silence, followup, approve, useful, useless"   the tuple has six

Every one was true when written. Every one was left behind by a change made
within the previous two days — and in each case the *docstring* next to the code
had been updated in the same commit that broke the README. The mechanism was
described twice, and the copy further from the code rotted first.

That is the actual mechanism of drift here, and it is the same one
`assert_copies.py` and `assert_design.py` were written for: two copies of one
fact, one of them unchecked.

### Why not check the prose too

Because a check that claimed to would be worse than none. There is no expression
over the source that decides whether a paragraph is still true, so such a check
would be a green light over a stale sentence — the failure this repository has
spent a week removing from its own numbers. `assert_docs.py` says in its own
docstring what it does not cover, so nobody reads a pass as "the docs are true".

### Why not delete the prose

A README that only points at code is useless to the person the README is for. The
line is not "say less", it is **say the part that changes rarely**. "A runbook
consolidates its cases at a threshold" survives a rewrite of how the draft is
applied. "The draft lands as proposal.md and waits for review" did not survive two
days.

Where a document genuinely must restate a fact the code also holds — a ceiling, a
set of valid values — make it machine-comparable and pin it. `assert_weight.py`
already did this: the README must state the ceiling, and the check reads both.
`ENUMERATED` in the new file is the same trick.

## Consequences

The enumerable class cannot silently rot again: a new route, a new knob or a new
member of a pinned set turns the gate red until it is written up or named as
deliberately unwritten. Verified by adding a seventh card-action kind and a new
endpoint and watching both fail.

The prose class can still rot, and will. What changes is that the surface is
smaller by policy and the failure is a wrong sentence rather than a wrong sentence
plus four missing features. The honest expectation is another audit in a month,
finding fewer things.

`ROUTES_UNWRITTEN` also turned an implicit choice explicit: twelve endpoints and a
dozen infrastructure knobs were undocumented not by oversight but because this
repo does not enumerate console plumbing and tuning limits. That was previously a
pattern one had to infer; it is now a list with reasons, which is also the place
to argue with it.

### What would change the answer

A drift that matters landing in prose the rule was supposed to protect — an
intent-level sentence going stale, rather than a mechanism-level one. That would
mean the split is in the wrong place, and the answer would be to move mechanism
descriptions out of the READMEs entirely and let them link.

## Follow-up, same day: the enumerable half is now generated, not checked

The section above says what would change the answer — "an intent-level sentence
going stale ... the answer would be to move mechanism descriptions out of the
READMEs entirely and let them link." That happened faster than expected, from the
other direction: writing `scripts/gen_reference.py` showed that the *check* was
the weaker tool. `assert_docs.py` could only ask whether a knob's name appeared
somewhere; it could not ask whether the sentence beside it was still true.

So the env half moved from checked to generated. `settings.py` holds a `#` comment
per field, `docs/reference.md` is produced from it, and `--check` fails on a
committed file that no longer matches OR a field with no comment. The second half
of that is strictly stronger than the rule it replaced: "appears in some README"
was satisfiable by a stale line, "has a comment beside the field" is not.

`ENV_UNWRITTEN` is gone. It existed to excuse thirteen infrastructure knobs from
being written up; all thirteen now have a one-line comment, which was less work
than maintaining the excuse.

### The trap this walked into first

Generating a table that lists every route and every knob makes
`assert_docs.py` VACUOUS, because its corpus is every `*.md` in the tree. Verified
by adding an endpoint nothing mentions: the check failed, the reference was
regenerated, and the check passed. A generated file now has to declare itself with
a banner, and `_corpus()` skips anything carrying it.

That is the same failure this file was written about — a green light over a fact
nobody had checked — arriving inside the commit meant to prevent it.

### What stayed hand-written, and why that is not a compromise

hookprobe's configuration table survived. It is not a reference: rows run to five
sentences explaining why `HOOKPROBE_EVENT_SECRET` is the one mutating route the
bearer token does not cover, and what the agent-proposes/service-signs division
buys. Generation would have replaced that with one clause per row, so the
generated table links from above it and the prose stays.

hookjudge's table did not survive, and should not have: nineteen rows of "ledger
path", "inbound body cap". That is a reference, and a reference is worth more
generated.

The line between the two is register, not subject matter — the same distinction
the parent decision drew between INTENT and MECHANISM, found again one level down.

### Cost

43 comment lines is 43 lines of source. hookjudge is now 2947 against a stated
ceiling of 3000. The next few knobs are affordable; a fortieth is not, and the
answer then is to raise the ceiling deliberately rather than to stop describing
things.
