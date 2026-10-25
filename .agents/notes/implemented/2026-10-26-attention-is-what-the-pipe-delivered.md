---
title: Attention is what the pipe delivered — judged, delivered and unconfirmed are three numbers
status: implemented
date: 2026-10-26
scope: hookjudge
---

## Decision

The judge records what the PIPE did with each verdict, and the attention figures
stop calling a judgement an interruption.

`mark_return` gained `delivered` (`1` routed, `0` suppressed, `NULL` not
reported) and `suppressed` (the pipe's `skip_code`). `_return_once` parses the
pipe's reply — the decision trace `handle_hook` already returns — and writes
both. A 2xx with `{"outcome":"routed"}` is a delivered card; a 2xx with
`{"outcome":"skipped","skip_code":"wake_no"}` is a judgement that produced no
card; a body the judge cannot read stays `NULL`. A dead return is `0`; a
`queued` retry stays `NULL` because a later attempt may still deliver.

`summary.attention` now carries:

- `judged` — verdicts recorded (the old number, now named for what it is);
- `interruptions` — rows the pipe routed to a channel;
- `conditions` and `repeats` — from the delivered rows only;
- `unconfirmed` — rows with `delivered IS NULL`, shown rather than assumed.

The noisiest list is delivered rows only. The board and `/metrics` say judged
and delivered separately, and the README table names all three states. The
judge ceiling moves to 3,700 for the columns, the reply parser and the tests.

## Why

The review's sharpest finding was that `interruptions` counted judgements while
the shipped pipeline dropped every `wake=no` card and folded repeats before a
card existed. The number was not merely approximate; it answered a different
question from the one the operator was making decisions with. The pipe's reply
is the only place the judge can learn the answer without reading the pipe's
ledger, and it was already being sent.

The migration is additive and leaves history `NULL`: those rows are reported as
`unconfirmed`, not counted as delivered and not silently zeroed. A deployment
upgrading will see `interruptions` fall and `unconfirmed` appear for its older
rows; that is the change being visible, not a loss of data.

## Consequences

- **A 2xx from the pipe no longer means "delivered" by itself.** The reply body
  does. An older pipe that returns a body without `outcome` leaves rows
  unconfirmed until it is upgraded; the judge does not guess.
- The board's "verdicts" tile and the repeat tile no longer describe the same
  population. The help text says why.
- `noisiest` can be shorter after the upgrade, especially on a deployment whose
  history predates the column: it only shows conditions with delivered cards.
- The pipe's response shape becomes a consumed contract. `handle_hook` already
  returned `outcome`/`skip_code` for every route; this makes the judge depend on
  it, so a pipe that changes those keys breaks the attention number — and a test
  in `test_app.py` now pins both the routed and the suppressed reply.
