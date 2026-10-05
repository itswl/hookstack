---
title: A review of the gate and the signer, and what it changed
status: implemented
date: 2026-10-05
scope: stack
---

## Decision

The two boundaries shipped on 2026-09-30 (the chat MCP gate and the watch
signer) were reviewed at full depth the same evening: ten independent readers,
one verifier per finding, a sweep for gaps. Thirty-six candidates came back,
none refuted. The ones that changed something:

- The timer signs from its own file again. `watch-timer` kept pointing at
  `probe-watch-secrets/watch.env` after PR #45 deleted it, so every contract
  violation since 18:20 that day failed to post with one log line. The key now
  lives in `work-data/watch-timer-secrets/watch.env`, a directory only the timer
  mounts — never probe-watch's, because that mount is what the agent reads.
  Routing the timer through the signer was rejected: no route to `probe_net`,
  and the signer refuses the timer's origin by design (see below).
- The scanner's own faults have a name the signer admits. The brief told the
  model to relay ⚠️ notes with `--origin`, and the signer refused every one of
  them, because the scanner's failure is never in a set the scanner failed to
  write. They are posted as `scanner / scanner-notes` now, admitted without an
  offer and forced to `low`/`note`: a source that cannot be read is not a quiet
  source, and a note at `low` funds nothing.
- The ceiling counts per window when the scan states no round. A no-prescan
  deployment keyed every post to round 0.0, the counter's own starting value,
  and was capped at twenty signals for the life of the process.
- The plan-approved door's secret is withheld from the agent. `handoff.py` had
  said since 2026-09-05 that the agent cannot reach that door because its
  subprocess does not inherit the service's secrets; `HOOKPROBE_HANDOFF_SECRET`
  was not on the list, the planner is on the pipe's network, the door checks
  nothing but that HMAC, and the bash guard does not police HTTP verbs. An
  injected plan could have started a work run with the write credential.
- `probe_net` sets the isolated gateway mode. `internal: true` keeps the
  bridge's gateway address, measured from inside probe-watch: on a Linux engine
  a host service bound to 0.0.0.0 was one connect away. Applying it recreates
  the network and every container on it.
- The gate and the signer: a bearer is compared as utf-8 bytes
  (`constant_time_eq`, now pinned across five files) instead of a bare
  `compare_digest` that died on a non-ASCII byte with no 401 and no ledger row;
  the token is checked before the body is read, the length is validated and
  bounded, and the handler has a timeout; `http.client.HTTPException` and a
  short answer are a 502 with a `call.failed` row rather than a dropped socket;
  a response the client posts back (no `method`) is passed through; `tools/list`
  filtering skips frames that are not objects and splits SSE on `\n` only;
  `call.forwarded` is written after the chat server answered, and the permitted
  half of a refused batch is recorded as `call.withheld`; the signer rebuilds
  the origin from its checked halves, refuses the checker's own producer, gives
  the slot back when the door refused, and treats any 2xx as delivered.
- probe-ingress binds every listener before serving, and `parse` refuses
  duplicate or out-of-range ports; both sockets of a pump carry the idle limit.
- The stale row "The watcher holds a signing key" left `docs/containment.md`
  (twenty-nine boundaries), the compose header and comments stopped describing
  container-side signing, the Chinese README's count is checked, the probes'
  MCP config has a tracked shape, the three probes' proxy block is one anchor,
  and the five sidecar test suites start their servers with a 20 ms poll
  (16.7 s to 1.1 s for the three new ones, on every gate run).

## Why

PR #45's own argument — a secret in an agent's environment is the agent's —
applied one hop later to the planner, and nobody had looked. The timer's
breakage was the same shape as the lesson AGENTS.md records: a value removed at
the point of production, with a second consumer nobody re-read. Both were found
by reading, not by anything failing loudly: the timer logged one line, the
planner's hole never fired.

The `scanner-notes` admission is a deliberate loosening, named as such in the
containment row: an injected round can always post one cheap note. The
alternative — the scanner writing a subject into `offered` on fault rounds —
cannot cover the round where the scanner itself died, which is the round that
most needs to say so.

## Consequences

- `work-data/watch-timer-secrets/watch.env` must exist on the operator's
  machine (one line, mode 0600); the compose comment says so. A deployment that
  has not created it gets the old failure, one log line per violation.
- Applying the gateway mode is a stack recreate, done at a quiet moment.
  Done 2026-10-05 09:46-09:50 on the work machine (OrbStack, engine 29.4):
  the option is accepted and the network is created with NO gateway address
  (IPAM reports none), which is the mode doing its job — yet `.1` on the
  subnet still answers "connection refused" from inside a probe. That is
  OrbStack's virtual router, not a Linux bridge interface, and nothing of the
  host is bound behind it; on this laptop the host is the VM either way. The
  claim "no route to the host" is read back here, and still has to be read
  back on a Linux engine before it is trusted there.
- The probe image carries the withheld-list change; the probes are recreated
  with it. The MCP config on each node's volume is unchanged.
- Second pass, the same day: the scanner now keeps the round before alongside
  the current offer (`previous` in scan.json), and the signer and the watch
  wrapper admit a conversation from either, counted against its own round (the
  counter holds the two newest rounds side by side, so a late post cannot
  reset the current round's count). One round wide and no wider: a run is
  capped at 30 minutes and cannot cross two ticks. And the producer half of an
  origin is a closed set where `WATCH_SIGNER_PRODUCERS` names one — the work
  compose names the chat tool, Jira and the scanner — so a round cannot label a
  chat finding as a Jira one; unset, it stays free text, the laptop's shape.
- Not changed, on the record: the `reported` cursor stays the agent's promise
  (the signer could write it, but that means mounting the watcher's state into
  the signer or moving the checker's source of truth — a decision for the
  operator, not a fix); the fixture subject in the signer's tests is a `demo-`
  name now, and the level/kind sets are the signer's own.
