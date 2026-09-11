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

**It cannot stay hidden, and detection is doubled rather than single.**
Simulated in a detached worktree at HEAD by both sessions independently —
delete `env=execution_env(),`, run the suite — and the ORDINARY component
pytest fails first:

    FAILED tests/test_remediation.py::test_the_environment_reaches_the_process_that_runs
    1 failed, 44 passed

`STACK GATE GREEN` never prints, and the gate exits 1. The guards checker's rot
check (`survives()` refuses to mutate unless `original.count(before) == 1`,
returning *"anchor appears 0 times — this entry has rotted"*) is the SECOND line
of defence, for anyone running that checker alone rather than the gate.

Worth stating because a reader will see two `GATE GREEN` lines in that run —
hookrelay's and hookjudge's component gates, both legitimately passing — and
"a green gate lied" is a recorded incident here. It did not lie this time: the
stack verdict is the one that counts and it never appeared.

## Consequences

* **The residual is narrower than it first looked, and the difference decides
  how much machinery it deserves.** Because the ordinary suite fails on a
  leftover mutation, the exposure is not "a bad commit that survives until the
  next guards run". It is a commit made **without running the gate at all** —
  which `AGENTS.md` already forbids in its first rule. "Somebody skipped the
  gate" and "the gate missed it" are different problems and only the second
  would justify rebuilding the runner.
* **The mitigation in force is discipline, and it is partial.** Staging explicit
  paths instead of `git add -A` removes this for the session that does it, and
  does nothing about the other two. Discipline that binds only the disciplined
  is not a control, which is why this is written down rather than agreed in
  chat — but combined with the doubled detection above it is proportionate.
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
