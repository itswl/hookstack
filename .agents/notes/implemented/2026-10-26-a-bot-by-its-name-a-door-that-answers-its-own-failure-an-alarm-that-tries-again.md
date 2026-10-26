---
title: A bot by its name, a door that answers its own failure, an alarm that tries again, and a rollback that says what it does
status: implemented
date: 2026-10-26
scope: stack
---

## Decision

A second deployment running copies of these files met four things in a week
that hold here too.

1. **The watch scanner recognizes a bot by its name.** `WATCH_BOTS` was the only
   way to drop a bot, so a bot nobody listed reached the digest as a message to
   answer. `BOT_NAME` drops a sender whose display name has bot or robot as a
   word of its own, a CamelCase `…Bot`, or 机器人; bot inside a word (Talbot,
   Abbott) is a person. The list stays, for bots whose names say nothing.
2. **A door answers and records a failure nobody planned for.** An exception a
   sidecar did not expect ended the handler thread: the caller waited out its
   own timeout, the ledger had no line, and the traceback went to stderr with
   no time. The shared HTTP handler now answers 502 and calls the door's own
   ledger hook (`call.failed` at the gate, `signal.failed` at the signer); the
   egress proxy refuses with 502 into its refusal ledger unless the tunnel was
   already spliced; every server logs an escaped error through the door's
   logger, with the peer. The proxy also writes the request line it forwards
   back as Latin-1, the way it read it: UTF-8 turned every raw non-ASCII byte in
   a path into two.
3. **An alarm tries a connection that did not happen again, in place.** The
   investigator's, the pipe's and the judge's alarms posted once, so a moment
   without a resolver dropped the page and only a later failure sent another.
   Two more attempts, two seconds apart; an HTTP answer is the channel speaking
   and is not retried; a timeout is, at the price of a possible duplicate.
4. **The releasing page says what a rollback does with newer rows,** and a
   release that changes a schema says it in its tag note. Read from the code for
   0.8.0 back to 0.7.1: nothing fails to open; a silence scoped to one alert
   quiets its whole source until it expires.

Fixed while verifying: the forwarder's bind test, changed while clearing a
CodeQL alert, clashed with a loopback socket, and a loopback socket does not
stop a wildcard bind with SO_REUSEADDR on macOS — the local gate was red on a
Mac while CI was green. It now clashes two forwarder binds, which fails on both.

## Why

Each was observed there, not reasoned: an unlisted bot became a to-do twice; a
query string with raw non-ASCII bytes killed a handler thread with no answer and
no ledger line; ten resolver misses in eleven days on the same kind of container
network, every one answered by a resend half a minute later; and a schema change
whose rollback nobody had written down. The code here had the same shapes. Each
fix has a test that fails without it.

## Consequences

- A person whose display name has bot as a word of its own is dropped from the
  watch digest. Nobody's is today; if it ever happens, the pattern is the place
  to change, not the list.
- `call.failed` rows can carry `reason: unexpected <Exception>` with a method and
  path instead of a client and tools; `signal.failed` is a new signer event;
  `egress.refused` rows can carry code 502 and `unexpected …`. The readers that
  filter on `signal.signed` or count refusals are unaffected.
- An alarm whose first attempts miss arrives a few seconds later, and rarely
  twice.
- The watch and sidecar changes reach a deployment at its next build from the
  checkout; the alarm changes in the three services reach the published images
  at the next release.
