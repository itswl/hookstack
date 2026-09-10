---
title: Measure the thing the design depends on, at the moment you cite it, from the machine the claim is about
status: proposed
date: 2026-09-10
scope: stack
---

## Decision

Three clauses, each earned by a distinct failure over two days. Where a change
rests on a measurement, the measurement has to satisfy all three, and the
easiest one to pass is the one that catches nothing.

1. **Measure the thing the design depends on**, not the thing that is easy to
   read. A field existing is not evidence the field is usable.
2. **Measure it at the moment you cite it.** A reading is true of a commit, not
   of a claim.
3. **Measure it from the machine the claim is about.** The refs, the `.env` and
   the running process are three different artifacts and only one of them is
   under your cursor.

## Why

**Clause one: a `target` field that existed and did not hold.** A remediation
cooldown was keyed on each step's declared `target`, on the reasoning that
`target` is what the model was asked to name — and the field does exist, in
`extract()` and in the prompt. Then the five proposals actually on production
were read: they name one thing three ways while running character-identical
commands. A target-first key would have cooled nothing on the only real data
there is. Fixed in `6615f3b` by keying on both halves, with the command named
as the half that holds. The measurement that disproved the design was one
`ssh` away the whole time and was taken after shipping.

**Clause two: a reading that was true when run and false when sent.** An egress
proxy bypass was measured under `readonly` — five commands, all `allowed` —
and quoted two commits later as current evidence, with an announcement that I
was taking `hookprobe/hookprobe/gate.py` to fix it. `guard.py:184`
`_ALWAYS_RULES` already refused all five, plus `wget --no-proxy` and the
backslash-newline continuation that models actually produce. Nothing acted on
the stale reading except me, about to edit a security-critical shared file
while a peer was mid-deploy. This is the worst-camouflaged version of the
error, because it arrives with output attached.

**Clause three, three times.** Production's commit was inferred from "we are
ahead" rather than read with `merge-base --is-ancestor`, twice — once
concluding a security fix was undeployed when it was live, once concluding a
day's work was undeployed when it was not. And `assert_shadow_config.py` run
locally reported a brain taking its model from a compose default: true of this
machine, whose `.env` is the WORK deployment's and has no judge at all, and
worthless as a statement about the shadow host. The same script cannot see what
it is asked about, which is why rules 3 and 4 of the deploy preflight live on
the host.

**And the count, which is the cheapest instance of all three at once.** Five
expired proposals were reported as three, from a listing filtered after
truncation. It changed no argument. It is in here because the fix costs one
word — read the whole set — and because two of us did it on the same day.

**The counterweight, and the one to copy.** `/unseen` answers in three values,
not two. Its first production run returned 20 checked, 5 seen, 0 unseen, **15
unknown** — all "bot is not the sender", cards posted by the chat app before
the switch. Two-valued, those fifteen would have been fifteen invented alarms
about messages people had probably read. The discipline above is a habit; this
is the same thing built into a return shape, which is strictly better, because
a shape does not need anybody to remember it.

## Consequences

**Re-run the measurement in the message or commit that cites it**, not in the
one that took it. Cheap for everything in this repository: the guards, the
boards and the ledgers all answer in under a second.

**Name the artifact, not the conclusion.** "Production is behind" is an
inference; `merge-base --is-ancestor <sha> <sha>` is a reading. "The field
exists" is a reading about code; "the field is usable as a key" is a claim
about data, and needs the data.

**Where a claim survives in writing, carry the measurement with the commit it
was taken at.** Several commit messages here already do it. That is the
mechanism-shaped part of this note and the only part a reader can check.

**Prefer a three-valued answer to a remembered discipline.** `unknown` beside
`seen` and `unseen`, `None` beside a cost and a zero, `unreachable` beside a
board and an empty one. Every one of those was added here after a two-valued
version told somebody something false, and none of them depends on anybody
having read this note.

**Nothing enforces this.** It is a formulation, like
`2026-09-10-a-wrong-number-costs-what-acts-on-it.md`, and it exists because the
alternative was six instances in two days going unnamed. If it earns a
mechanism, the shape is the last consequence above rather than a check: build
the third value, and the discipline stops being needed.
