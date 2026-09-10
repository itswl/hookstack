---
title: What happens when somebody just keeps typing — four silent refusals and a fold nobody saw
status: implemented
date: 2026-09-10
scope: hookprobe
---

## Decision

Two changes, both found by being asked a plain question: *what happens if I keep
replying and the context gets too large?*

**1. Three of the follow-up door's refusals now answer in the thread.** A
conversation at its ceiling (20), a window with no budget left, and a session
that can no longer be resumed each return a report-shaped notice through the
family loop, carrying `thread_root` so it lands as a reply. Keyed off the
message id, so the platform's redeliveries collapse onto one notice.

The other three stay quiet, and the line is whether the person could act on
knowing: an unknown thread and an already-answered redelivery are the expected
steady state, and telling an unlisted sender that they are unlisted answers a
question they should have to ask a person.

**2. A report whose context was folded says so on the card.** `folded()` counts
the session's compactions and `with_fold_note()` appends one line to the summary
the channel renders.

## Why

The honest answer to the question turned out to be: **you hit the follow-up cap
long before the context, and the twenty-first message vanishes.**

The door's own docstring says every refusal is "a 200 with a reason: the pipe
records it". That is true and it is not the point — the pipe records it in a
**ledger**, because a 2xx from this service is a delivered delivery
(`channels.send` returns `True, "http 200"` and the body goes nowhere). The
channel records nothing. So somebody who keeps typing watches the bot go quiet
with no way to tell being declined from being broken.

This is the third instance of one defect this week, which is what makes it worth
a note rather than a commit message. The superseded-procedure refusal had it.
The budget breaker had it years earlier and solved it — refuse, then say so as a
report through the family loop — and that solution is what both of the others
now reuse. **A refusal that the refused party cannot see is not a refusal, it is
a silence with a good reason attached.** Nothing in the tests caught any of them,
because a test asserting `{"status": "skipped"}` passes whether or not a human
ever learns anything.

On compaction: the fact was recorded from the day the `PreCompact` hook was
written, and the hook's own comment says why it matters — *"a report with a gap
in the middle of a long investigation has no other explanation available
afterwards"*. It was shown on the console's turn line (`context folded 2×`),
which is not the surface anybody is looking at while they read the answer in a
chat window. The note deliberately does not claim the answer is wrong; a folded
conversation is usually fine. What a reader loses is the ability to assume the
answer saw everything, and that assumption is the one worth taking away.

**What was NOT done, and why it is the opposite of what the review asked for.**
The proposal was Larkin's *Rearm & Replay*: at ~80% context, kill the session,
start a clean one, replay a distilled brief, and save ~90% of the cost. On this
system the sign is inverted. Measured pair on the same context: **$0.1490 fresh,
$0.0156 on the follow-up**, and every investigation carries ~29k input tokens of
prefix before its alert is mentioned. A rearm throws away the warm prefix and
pays that entry fee again. `hookprobe/docs/cost.md` states the opposite habit
outright, and the storm-coalescing design already joins re-fires into the
existing session for exactly this reason. What was real in the proposal was the
*quality* half — that a folded conversation has lost something and nobody is
told — and that is what shipped.

**3. And a report delivered after its condition ended says so.** Same shape as
the fold: `with_recovery_note()` appends one line naming when the condition
ended relative to the report being finished.

This one came from a review's "split-brain" scenario, and it is the only shape
of stale card this system can actually produce. `record_recovery` finds the run
whether or not it has finished (`list_runs` holds in-flight runs too) and
annotates it; it starts no turn, spends nothing, and therefore moves nothing the
freshness cursor watches. So the cursor — which guards the APPROVAL of a
procedure — cannot see it, and nothing guarded the DELIVERY of the report. Before
this, `notify.py` contained the string `recovered` exactly zero times.

An admission, not a suppression, and the boundary is worth stating: a proposal
created *after* the recovery is stamped as already-recovered, so `moved()` sees
no change and its approve button is still drawn. That is deliberate — the
operator may still want the fix — and this line is what tells the person
pressing it what they are pressing.

**4. And a message the PIPE declined is told so too.** The three fixes above are
all in the probe; this one is a layer earlier. `unknown_thread` (a reply under a
card nobody here sent) and `no_route` now come back as one line in the thread,
sent by the bridge — the only component that can speak — and only for messages
it decided to forward in the first place.

Deliberately just those two codes. An unrecognised code stays silent rather than
being explained by a guess, and a routed message is not commented on at all.
The read of the pipe's answer had to grow from `read(300)` to the whole body:
300 bytes was enough for the log line it was written for and truncates the JSON
this now parses, which would have failed silently — the same defect, one layer
in, caught before it shipped. And a failed explanation is swallowed: the caller
reports "forwarding the thread reply failed", and turning "we could not explain
ourselves" into "your message was lost" is worse than the silence being fixed.

## Consequences

* A refused reply now costs one card in the chat. That is a real increase in
  traffic on a chatty deployment, bounded by being idempotent per message and by
  only three of six refusals qualifying.
* The follow-up cap is now visible, which will make it look low. It is 20, and
  `_MAX_FOLLOW_UPS_PER_RUN` is the knob; nothing about this change argues for a
  different number, it only stops the number being enforced in secret.
* The notice is a `notice` run (see the superseded-procedure note): folded out of
  work items, offered no buttons, never listed as unruled.
* Still unanswered by anything: how full the context actually is. The probe
  latches off on this deployment's CLI (2.1.259 does not answer
  `get_context_usage`), so `run.context` is null and the fold count is the only
  signal there is.
