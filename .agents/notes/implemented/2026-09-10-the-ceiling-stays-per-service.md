---
title: The weight ceiling stays per-service, because a per-module cap would price the decomposition
status: implemented
date: 2026-09-10
scope: stack
---

## Decision

`scripts/assert_weight.py` keeps one ceiling per service. Not per module.

This answers the question the 2026-09-04 entry left in the file after three
raises in one day:

> Whoever touches this next should decide whether the measure should be
> per-module rather than per-service, and record that decision in
> `.agents/notes` rather than raising a fourth time in silence.

## Why

The reasonable case for per-module is real: a 5,650-line ceiling says nothing
about `app.py` being 900 lines, and "one person can read this end to end" fails
long before the total does if it is all in one file.

It is still wrong here, for one reason that outweighs it. **A per-module cap
prices the decomposition**, and the decomposition is the thing the budget must
never argue against. The correct answer to "this module is too big" is often a
new module — and under a per-module cap that move costs nothing while the
alternative (leaving it) costs nothing either, so the check stops measuring
anything; or, worse, the caps are set tight enough to bite and then a legitimate
split is a negotiation with a script.

The measure exists for a property of the SERVICE: *small enough that one person
can read it end to end before changing it safely.* That is what an owner
constrains, that is what a README can honestly claim, and it is the same shape
as the promise beside it — "five dependencies, chosen to stay five" is a fact
about the service, not about any file in it.

Two smaller reasons, recorded so the next person does not re-derive them:

* **A per-module list is a second thing to maintain.** Every new module needs a
  number chosen by somebody, and a number chosen for a file nobody has written
  yet is a guess that will be raised on its first real change — which is the
  formality this file already warns about.
* **The failure it would catch is caught better by reading.** `assert_weight`'s
  own docstring is blunt that lines are "a proxy, and a coarse one". Structure
  is exactly the axis it does not measure; a check that pretends otherwise trades
  a known-coarse signal for a false-precise one.

What would change this decision: a service growing past its ceiling by adding
modules that are each individually fine — i.e. a total that keeps rising while
no single change is arguable. That has not happened; every raise so far names
one feature.

## Consequences

* The ceremony is unchanged and stays deliberately cheap: raise it in one line,
  in `CEILINGS`, with the reason, and state the same number in the service's
  README (the check enforces both halves).
* The 2026-09-04 comment's request is now answered, so the next raise is a raise
  and not an open question.
* `hookprobe` stays uncapped by design — it carries Node and the CLI, and its
  own entry says adding a ceiling there is a product decision, not a tidiness
  one. Nothing here changes that.
