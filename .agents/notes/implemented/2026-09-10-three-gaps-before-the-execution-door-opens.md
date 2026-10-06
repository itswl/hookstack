---
title: Three gaps closed before the execution door was ever opened
status: implemented
date: 2026-09-10
scope: hookprobe
---

## Decision

Three gates around the remediation executor. None of them opens the execution
door; they are what should already be true on the day an operator first writes
a line into `HOOKPROBE_REMEDIATION_ALLOWLIST`.

1. **A per-target cooldown.** A target another procedure has acted on is left
   alone for 15 minutes (`HOOKPROBE_REMEDIATION_COOLDOWN_SECONDS`, `0`
   disables), and one still `running` holds its target with no window at all.
   Keyed on a step's declared `target` **or** its literal command, either one
   matching.
2. **`risk` stops being decoration.** A step the report itself marked
   `risk: high` must full-match a pattern in
   `HOOKPROBE_REMEDIATION_HIGH_RISK_ALLOWLIST` as well as in the ordinary
   allowlist — deny-by-default in the same direction, both files re-read before
   every step, one uncovered high step refusing the whole procedure at the
   click.
3. **The executor gets a constructed environment.** `execute()` passes an
   explicit allowlist of variables instead of inheriting the service's.

Deliberately not done, each for a different reason:

* **Negative-feedback verification** ("did the fix make it worse") is worth
  having and cannot be shaped the way it is usually drawn. The production
  investigator holds no credentials for the systems it writes procedures about,
  and a watchdog that opened one would be a second, unaudited way of touching
  them — the same constraint the freshness cursor row already states. It has to
  read the pipe's NEXT signal, not probe the target.
* **Proposals as pull requests** is a door, not a lock: this agent's bearer is
  refused on every method but `GET`, and the input guard exists to stop a run
  installing its own instructions. Giving it an outbound write path is a
  separate project with its own note, and it is the one idea in this family that
  NET INCREASES the attack surface.
* **Kernel-level syscall enforcement** as observation, yes; as `SIGKILL`, no —
  see [[two-person-review-needs-a-second-person]].

## Why

**The door had never been opened.** Measured on the production deployment,
read from inside the running container rather than from a compose default:

```
HOOKPROBE_REMEDIATION_ALLOWLIST = []      (empty: deny-by-default, nothing runs)
HOOKPROBE_AUTOMATION_TIERS      = []      (defaults; remediation = propose)
proposals on disk               = 5, every one of them `proposed`
automation ledger, remediation  = 5 proposed / 0 approved / 0 dismissed
```

That number decided the ordering. An entire class of hardening — ephemeral
credentials, sandboxed runtimes, second-approver rules — protects a path that
has executed nothing, ever. What was worth building first was not another lock
on that door; it was the three things that would be *missing* the first time
somebody opened it.

**The cooldown, and the claim in the first draft of this note that was wrong.**
That draft said *"the cooldown needs a target and a proposal has none; inferring
one from the step commands is a guess dressed as a key"*. Both halves were
false. `target` has been in the step schema since the first commit, and the
report prompt has always asked the model to name what each command touches.
Every one of those answers was stored and read by nothing.

What is true is narrower and more useful: the field is populated and **not
sufficient alone**. The five proposals on production name one thing three ways —
`AWS SES 账户状态`, `AWS SES account status`, `AWS SES 账户状态（只读）` — while
`aws sesv2 get-account --query SendingEnabled` appears verbatim in five of their
eight steps. Keying on the label alone would have cooled nothing on the only
real data there is. Keyed on target OR command, marking one row executed holds
the other four, two of them by the command only. The label is what a person
reads; the command is what holds.

**The executor's environment.** `create_subprocess_exec` was called with no
`env=` at all, so a command an operator approved ran with the family's HMAC
signing keys, the Lark app secret and the provider credential in scope. Three
things stood in front of it — deny-by-default allowlist, a human click, no
shell — and none of them is a reason to hand a procedure keys it does not need.
An allowlist of variables rather than a denylist of secrets, because what a
denylist cannot cover is the secret a future deployment adds under a name
nobody wrote down.

**`risk` had been decoration for as long as `target` had.** In the step schema
from the first commit, validated on the way in, and consumed in exactly two
places: a colour in `ui.html` and the word in the approve button's label.
Nothing gated on it.

The ceiling on what that gate can be worth is the point, and it is stated
wherever the gate is documented: **the risk label is written by the model.** So
the gate can only ADD a requirement, never catch a missing one. The case that
matters most — a dangerous command the report called `low` — is bounded by the
ordinary allowlist exactly as it was before, an operator-written full-match
pattern. What the second file buys is the ability to reserve a stricter list for
the commands the model is *willing to call dangerous*. That is a real thing to
want and a smaller thing than "dangerous commands need two patterns", which is
how a row like this gets misread a year later.

A second allowlist rather than the two alternatives considered: a longer
cooldown for high-risk steps is a rate limit, not a gate, and a mislabelled step
would only get the wrong window; binding it to the automation tier is per-class,
not per-step, so `high` would be either always or never runnable. The file has
the same shape as the gate that already exists, which means an operator learns
no new concept.

**One thing worth naming so nobody misreads it.** `automation.permits()` is
still called in exactly one place — `service.py`, for `memory`/`auto_apply`.
The `remediation: propose` ceiling in `_DEFAULT_TIERS` is declared and consulted
by nothing. It is not a hole: there is no auto-apply path for remediation to
exceed it with, so the ceiling is vacuously true. It reads like an enforced gate
and is not one, and this change did not make it one.

**And what the review that prompted all three got half-right.** The critique
that a regex allowlist is still imperative — "the interpretation is left to
string matching" — missed that `execute()` lexes each command into an argv and
never touches a shell, precisely so a pattern written with a wildcard cannot
hand the span to `/bin/sh`. Full-match rather than search closes the other
classic. What genuinely remains is an operator pattern written too wide, and the
proportionate answer is to turn the `command` string inside the existing fenced
`remediation` block into an `action` + `params` pair — the block is already a
schema with one free-text field — rather than to build a parallel typed surface.

## Consequences

* **Still collect-only.** Nothing here makes anything newly runnable, and the
  high-risk list makes one thing *less* runnable than before: a `high` step the
  ordinary allowlist covered is now refused until a second file covers it too.
  Zero blast radius today, because nothing has ever been approved — which is
  the argument for landing it now rather than after the first pattern is live.
* **The check that keeps the containment page honest had gone silent, and this
  change found it.** `_BOUNDARY_EN` in `assert_docs.py` enumerated the number
  words and stopped at twenty-five while `_TEENS` learned twenty-six, so both
  English pages matched nothing and dropped out of the comparison — while the
  report still read "26 containment boundaries match every page that counts
  them", counting one page of three. It now matches any word and reports an
  unreadable one as a failure, so the next word off the end is loud instead of
  silent. Same shape as [[three-shapes-of-a-write]]: a claim wearing words
  bigger than its implementation, found by checking rather than re-reading.
* **What is verified, and what is not.** The cooldown was read back on
  production after its deploy — all five rows report `cooling: ''`, so nothing
  is falsely held on day one. The high-risk gate was read back on production at
  `5a00dfd`, in two parts, because the first alone would have proved less than
  it looks: the knob now ARRIVES (`printenv` exits 0 with an empty value; before
  the compose line it exited non-zero), and the DEPLOYED IMAGE's own
  `step_deny_reason` refuses a `high` step with no high-risk file — *"step
  declares high risk and no high-risk allowlist is configured"* — while passing
  the identical command labelled `low`. That is a refusal observed on the
  artifact rather than on a laptop.
  **What it still does not show is the gate standing in the path of a real
  approval.** `HOOKPROBE_REMEDIATION_ALLOWLIST` on that host is empty, so no
  proposal reaches any gate: deployed and answering, never exercised in anger.
  It stays that way until an operator arms remediation, and this bullet should
  be the first thing revisited when one does. **Revisited 2026-10-06:** the
  work deployment's write node now carries both knobs and the operator's file
  mount ([[arming-the-work-executor]]), so an operator CAN arm it with one
  line; the sentence above stands until the first real press — which is now
  the moment to check this bullet again.
* The declined half of the same review is in
  [[two-person-review-needs-a-second-person]].
