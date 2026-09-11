---
title: The mutation checker breaks tracked files on purpose, and three sessions share the tree
status: proposed
date: 2026-09-11
scope: stack
---

## Decision

Leave `assert_guards_are_tested.py` mutating files in place for now, and record
the residual rather than build around it. The proportionate fix, when somebody
takes it, is to run the mutations against a COPY of the package and tests with
`PYTHONPATH` pointing at the copy, so no tracked file is ever wrong on disk.

Not taken today, and the reason is the same one that kept a `_SECRET`-shaped
rule off the reachability checker the evening before: this is the tool three
sessions trust to tell them whether the containment claims are real, and a
subtle mistake inside it is worse than the window it would close. It wants its
own pass, awake, not a bolt-on at the end of a long one.

## Why

**The window is real and was observed, not imagined.** On 2026-09-11 two
sessions independently saw `hookprobe/hookprobe/remediation.py` modified and
uncommitted in the shared tree and neither had touched it. The diff was
byte-identical to a mutation entry: deleting `                env=execution_env(),\n`
is the `after` string for the claim *"an approved command inherits the service's
secrets"* (`assert_guards_are_tested.py:115-119`). It was a guards run
mid-flight, and it restored itself.

**The restore is exception-safe already.** `survives()` copies the file into a
`TemporaryDirectory` and restores in a `finally`, so an exception or a failing
subprocess cannot strand a mutation. Only a hard kill of the runner can, and
nothing observed suggests that happened.

**What is left is concurrency, not correctness.** Seventeen mutations, each
running a pytest subset, means some tracked source file is deliberately broken
for a large fraction of a gate run. In that window a `git add -A` from another
session in this tree commits a deleted containment boundary. That command has
been used twice in one day here, and the tree has three sessions in it.

**It cannot stay hidden, which is why this is a note and not a patch.**
`survives()` checks `original.count(before) != 1` before mutating and returns
*"anchor appears 0 times — this entry has rotted"*, so the next run fails loudly
and names the entry. The exposure is the gap between the bad commit and that
run — which can include a push.

## Consequences

* **The mitigation in force is discipline, and it is partial.** Staging explicit
  paths instead of `git add -A` removes this for the session that does it, and
  does nothing about the other two. That asymmetry is the whole argument for
  eventually copying rather than mutating.
* **A copy-based runner is not free.** The venv installs hookprobe, so the copy
  has to win the import — `PYTHONPATH` ahead of site-packages, `cwd` at the
  copied tests. Cheap to write, easy to get subtly wrong, and wrong here means
  the checker reports `caught` for a guarantee it never tested. See the
  `PYTHONPYCACHEPREFIX` comment in `survives()` for the last time that exact
  failure happened.
* **Whoever takes it should keep the loud-on-rot check regardless.** It is what
  bounds this residual today and it would still bound the next one.
* Related: [[measure-the-thing-the-design-depends-on]] — both sessions here
  reached for a theory (a hard-killed run) before reading the mutation list,
  and the list settled it in one grep.
