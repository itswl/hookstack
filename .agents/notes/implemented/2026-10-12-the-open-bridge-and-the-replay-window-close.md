---
title: The open bridge and the replay window close, both fail-closed
status: implemented
date: 2026-10-12
scope: stack
---

## Decision

Two boundaries that were shaped so an operator had to remember something are now
shaped so forgetting fails closed.

1. **The bridge refuses to start with an empty `BRIDGE_INBOUND_SECRET`.** It used
   to accept every card, and `docs/containment.md` recorded that as the *shipped
   default* — the reason both deployments set the secret. A deployment that
   genuinely has nobody untrusted on the wire now says `BRIDGE_ALLOW_UNSIGNED=1`
   and accepts everything on purpose, out loud.
2. **The pipe claims each inbound signature once.** `security.replay_token` +
   `Store.claim_signature` answer the second copy of a signed request as a
   duplicate *before* the fuse and the pipeline, so a capture replayed inside the
   300-second skew cannot buy a second verdict. `HOOKRELAY_SIGNATURE_REPLAY_SECONDS`
   is the window (default 300; 0 turns the cache off and leaves the old behaviour).

The `HOOKPROBE_POSTURE_CHECK=enforce` default, which the containment page used to
read as an opt-in, is now spelled out in the deploy composes. The code already
defaulted to `enforce`; only the documents disagreed.

## Why

Both rows in `docs/containment.md` named the gap themselves, and a gap that is
safe only while an operator remembers is one the next deployment will not have.
That page's own thesis is that a boundary with limits written down is worth more
than one trusted for more than it holds — but writing the limit down is not the
same as closing it, and two of these were closable in the schema and a startup
check.

The replay cache closes the last hole in the old argument. `verify_signature`
said a replay "mostly arrives as a duplicate and is skipped as one" — true only
inside the 120-second content-dedup window. An alert replayed at t=200s is inside
the 300-second skew and *outside* the content window, and was a fresh judge call.
The second identity (`replayed_signature`, keyed on the signature) folds exactly
that case without touching the first (`duplicate`, keyed on content).

The bridge change is a reversal of a shipped default, which is why it has this
note rather than a comment: the old behaviour was documented, deliberate, and
wrong for anyone but the one operator who knew to set the secret.

## Consequences

- Two knobs to know: `HOOKRELAY_SIGNATURE_REPLAY_SECONDS` and
  `BRIDGE_ALLOW_UNSIGNED`. Both are reachable from a compose and documented in
  the reference tables the gate generates.
- A legitimate sender that retries an identical signed request inside the skew
  now gets `{"outcome": "skipped", "skip_code": "replayed_signature"}` instead of
  a recorded `duplicate` row. Same HTTP shape, one fewer ledger row; the sender's
  view is unchanged.
- Plugin adapters that speak their own header (`X-Hub-Signature-256`, Stripe) are
  **not** tracked — `replay_token` says so rather than guessing, so those doors
  keep the bounded window. Closing them means teaching the adapter its own replay
  key, not widening this one.
- The bridge's in-body legacy sign still covers only the timestamp, so its
  five-minute window stays bounded, not closed. The containment page says so.
- The pipe grew past 6,300 source lines, so `CEILINGS['hookrelay']` moved to
  6,400 with the doctrine answer recorded beside it: the door is the pipe's job,
  it reads the pipe's own header and never a payload.
