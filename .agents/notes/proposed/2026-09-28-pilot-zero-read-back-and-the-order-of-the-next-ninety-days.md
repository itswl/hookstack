---
title: Pilot zero read back — three locks on the approve door, and the order of the next ninety days
status: proposed
date: 2026-09-28
scope: stack
---

## Decision

Four decisions, taken 2026-09-28 by the operator after a memo proposing to
converge hookstack on "alert investigation plus human-approved remediation for
SRE teams" was read against the archive of the retired production deployment
(pilot zero, 2026-08-03 to 09-23).

**1. The next ninety days run in this order, and each step gates the next.**

1. *Honest instruments first.* A patrol's ruling stops counting as a person's
   verification ([[a-patrols-ruling-does-not-verify-the-work]], shipped with
   this note). The weekly page is read against this before anything below is
   measured with it.
2. *A first run that shows the whole loop with no key.* A replay adapter in
   `hookprobe/runtimes.py` — a recorded investigation played back through the
   real gate and the real flight recorder, priced at nothing, marked synthetic —
   so the quickstart's investigator is up by default; the demo pipe offers
   `approve` and `followup` on its cards; `demo.sh` ends with a report, a card
   whose approve press executes an allowlisted observation, and the pipe's
   audit page of the whole chain. Not a fourth runtime: a rehearsal that runs
   no model and executes nothing the script did not record.
3. *One real approved remediation on the operator's own system.* An allowlist
   of one line, a write credential held by the work node only, an approval that
   records who pressed it as a field rather than a note, and the recovery
   contract of [[execution-success-is-not-recovery]] shipped so the loop closes
   on the condition's evidence rather than on exit codes.
   *Reached on 2026-09-30 through the handoff, not the allowlist:* a plan a
   person handed off, carried out on a test cluster by the work node with a
   write credential of its own (`docs/deployments.md`, "The first real write").
   The credential was an administrator profile, mounted for the run and taken
   off after. The allowlisted procedure and a credential scoped to one job are
   still open. *The wiring landed 2026-10-06* ([[arming-the-work-executor]]):
   the write node carries the executor's gate knobs and the operator's file
   mount, and the approve press is its console. What remains is host-side and
   additive — one allowlisted line, a credential scoped to the job, and the
   first approved procedure; the porting table's hash-binding row stays open.
4. *Thirty days on the operator's own alert feed*, the weekly page read every
   Monday, with the two attention numbers below required to move the right
   way before anyone outside is asked to run it.
5. *Only then* two or three teams that are not the operator's.

**2. Converge on the loop, not on the signal.** The loop is signal in, read-only
investigation, a plan, a person's approval, contained execution, verification,
record. "Alert" stays the front page's story
([[the-investigator-is-the-product-and-the-front-page-says-so]]); the work shape
stays deployed and stays in the code. Nothing is removed to "focus": the two
shapes share every line of Python and differ only in config.

**3. What comes over from the successor project, and when.** airlock
(`~/Documents/airlock`, the operator's, 2026-09-23) built the same loop from
scratch and is the source of patches, not the successor (operator, 09-24).

| from airlock | into hookstack | when |
| --- | --- | --- |
| the report's full text follows the card into the chat thread; a proposal card lists every step; a console link only when the console is reachable | `notify.py`, the bridge | step 2 (the phone is the console) |
| the approver's identity on the approval record | `remediation.approve` gets an `actor` field; the console press carries the bearer's name | step 3 |
| the approval binds to the version and hash of what was read | proposal supersession already exists; add the hash to the card and the check to the door | step 3 |
| a gate in front of the chat MCP's writable credential (`mcpgate`) | the work stack's compose | step 3 |
| the watcher's signing key out of the agent's reach | `post_watch_signal.py` | step 3 |
| the launcher's per-plan ephemeral worker with only that profile's credentials | not built. The door first opened on 2026-09-30, with an administrator profile far wider than its job; that is what the blast radius argument is worth | after step 3 |
| the offline demo (`scripts/demo.py`: stub engine, plan, comment, v2, approve, ledger) | the shape of step 2's rehearsal | step 2 |

airlock takes no new features; it stays the reference and the test bed.

**4. Unchanged, already decided:** one team, one deployment
([[one-config-one-token-one-team]]); no fourth model runtime, no new chat
platform in the pipe, no general task management, no incident model until a
person is there to work in it.

## Why

**Pilot zero already ran, and its operator stopped using it.** The memo asked
for pilot teams; the archive says what happened to the first one.

| | W34 (08-17) | W35 | W36 | W37 (09-07) | W38 (to 09-20) |
| --- | --- | --- | --- | --- | --- |
| judge verdicts | 326 | 316 | 476 | 634 | 203 |
| cards marked wake=yes | 138 | 13 | 167 | 354 | 59 |
| of those, from the loudest single rule | 35 | 4 | 139 | 286 | 51 |
| investigation runs | 53 | 31 | 83 | 22 | 14 |
| investigator spend | $35 | $22 | $21 | $0.41 | $0.08 |
| card presses by a person | 9 | 0 | 0 | 0 | 0 |

The person's whole footprint over seven weeks: nine presses and three answered
questions, all on the evenings of 2026-08-20 and 08-21. Never again — not in the
quiet week that followed (13 wake=yes cards), not in the flood (354). Volume did
not drive them away; they had already gone. Of the cards delivered to the chat
in the retained window, about a third arrived between 23:00 and 07:00 local.

**Three locks on the approve door, none of them a person's choice.**

1. *The button was never drawn.* The production pipe's `card_actions` offered
   `useful`, `useless`, `silence` and `remember`. Not `approve`, not
   `followup` — "a card cannot grow a button nobody configured", and nobody
   configured it. 741 cards in the retained window carried exactly five button
   labels; no approve, no "Ask why". The investigator declared the buttons on
   every report that proposed something; the pipe dropped them by config.
2. *The console was not reachable from where the cards were read.* The
   investigator's public URL was empty on the deployment; 3 of 741 cards carried
   any link at all. The summary was the card and the full report stayed on the
   session, so the person read a one-paragraph conclusion on a phone with no way
   to see the evidence behind it, and no way to reach the console where the
   approve button actually lived.
3. *An approval would have been refused.* `HOOKPROBE_REMEDIATION_ALLOWLIST` was
   empty for the deployment's whole life. Every press, had one been possible,
   would have answered "no allowlist configured; proposals collect, nothing
   executes".

So "five proposed, zero approved" measured the configuration, not the person.
The five proposals were for one mail-bounce condition, within twenty hours on
09-07/08, one or two steps each, and the patrol ruled that condition's
investigations useless the same day.

**The seven rulings a person did give left no trace.** On 08-21 the person ruled
seven judge cards worth or not worth waking for. The pipe recorded each as
"forwarded to the judge"; the judge's ledger holds zero `mattered` rows, ever.
The rows both sides would have needed are gone from both ledgers, so the cause
cannot be read back now; the fact stands, and it is the shape `AGENTS.md`
names — a value computed correctly and dropped on the way to where it acts.

**The machine took the person's job the next day.** The run-rulings patrol
filed its first inferred verdict on 08-20, the day of the first press. From then
on every "useful" and "useless" on the weekly page was the model's, and the board
counted them as verification ([[a-patrols-ruling-does-not-verify-the-work]]). A
person reading the page saw the work being graded without them.

**The cost problem was solved; the attention problem was not.** Investigator
spend fell from $21–35 a week to under a dollar in W37, when families the node
had no instrument for were declined at the door
([[a-family-with-no-instrument-is-declined-at-the-door]]): 111 declines that
week. In the same week one rule produced 286 of 354 wake=yes cards. The lever
that worked was refusing work; the lever that did not exist was folding a
flapping rule.

**What the memo got right and where it was thin.** Right: the single loop; the
first-run experience; the pilot metrics — which the weekly page already computes
(interruptions, wake yes/no, the loudest condition, median time to first result,
rulings, false quiet on the golden set, closed unattended, cost). Thin: it did
not read pilot zero; it converged on the signal where the code converges on the
loop; it was silent on airlock, whose README is the memo's core loop already
built; and it stacked pilots on a remediation leg that has never executed
anything for real. Its four "minimal capabilities" sort as: state semantics —
built (`verified_by` distinguishes ruling, remediation, recovery); approval audit
— half (a card press writes the presser into a free-text note, a console press is
anonymous); one attention entry — half (the alarm channel pages node-wide
failures, the weekly headline posts itself, the blocked count is per node);
owner/assignee — deferred twice and still last
([[nobody-owns-an-alert-and-no-component-picked-it-up]]).

## Consequences

Step 2 needs a replay adapter that passes the runtime contract suite, a demo
pipe config that offers `approve` and `followup`, an allowlist shipped inside
the investigator's image for the rehearsal, and a release, because the
quickstart runs published images. Step 3 needed a write credential. None was
minted for it: the first real write, on 2026-09-30, ran on the operator's own
administrator profile, mounted for that run and removed after. It was the first
time the containment story met a live system.
Step 4 needs the alert shape running somewhere again: the platform's forward
rules toward this stack are disabled, not deleted, and point at a host that no
longer runs it.

The two numbers step 4 must move before step 5: wake=yes cards per week reaching
a person, down, with the loudest rule folded; and rulings by a person, up from
zero. The north star `closed_unattended` is read only after
[[a-patrols-ruling-does-not-verify-the-work]] has been in the page for the whole
window.

Revisit triggers, as in the positioning note: a deployment that is not the
operator's, a piece of work spanning two nodes, a person who answers the cards.

## Rejected

- **Pilot teams first.** The first pilot's operator stopped pressing within two
  days and nothing in the memo asks why. A second team would meet the same three
  locks.
- **"Only alerts" as a code boundary.** Removes nothing (the shapes are config)
  and switches off the one deployment in use, the work shape.
- **Porting airlock's launcher and per-plan containers now.** Hardening a door
  that has never opened, in the wrong order; the argument for it is made by the
  first real execution, not before.
- **Owner/assignee first.** On a one-team deployment where nobody answers, a
  field saying who owes the next step changes no behaviour; the phone-readable
  card and the drawn button do.
- **A fifth chat platform, tenancy, a fourth model runtime.** Already decided,
  and the memo's "do not build" list agrees with the notes that decided them.
