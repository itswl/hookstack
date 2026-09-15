---
title: The platform's alert page sees the investigations the pipe started on its own
status: implemented
date: 2026-09-15
scope: stack
---

## Decision

A source may name the sending platform's own id for an event
(`reference: "{meta.event_id}"` on the `ww` source). The pipe extracts it
beside the fields, keeps it on the event, and hands it to brains as a top-level
`reference` in the normalized payload. The investigator keeps it in
`meta.reference` and echoes it on its report. A new generic channel,
`to-platform`, delivers the investigator's report envelope verbatim and signed
to the platform's report intake, on the same route that delivers it to the
person (`report-to-me` sends to both). The platform answers 200 to an envelope
without a reference, so a report about nothing of its own is recorded, not
retried.

## Why

The platform's own investigation leg was switched off on 2026-09-14 because two
legs were funding the same investigation. From then on every investigation of
a relayed alert started here, on the judge's verdict, and its report reached
the person's chat and this console — and never the platform's alert page,
which had shown reports inline for as long as the platform had asked for them.
The operator's question was literally "how do I get a report now".

The pipe had the platform's event id all along: it arrives in the relay
envelope's `meta.event_id` and sits in the ledger's payload column. What it
lacked was a place to carry it forward that would not change any judgement.
Fields were the wrong place — they feed the judge's identity, and an id that
differs on every event would split each firing from its recovery — so it is a
column and a top-level key of its own, and it is the platform's id, not this
pipe's, because the platform is the one that has to look it up.

The return leg is a channel, not code: hookrelay already speaks
`payload: raw` (the original inbound bytes, signed with the family's
timestamped HMAC), and the investigator's report is already the processed
dialect. Nothing in the pipe knows what the platform does with it.

## Consequences

- One more column on `events` (`reference`, nullable, migrated additively);
  one more key on the normalized payload when the source names one; one more
  key in the investigator's report meta (`reference`, "" when none).
- Two deployment values on the pipe's host: the intake URL and the secret it
  verifies (mirror of the platform's `DEEP_ANALYSIS_REPORT_INTAKE_SECRET`).
- An investigator run opened from the platform door with `_meta.reference`
  and `_meta.notify` also files back, which is how the wiring was verified
  without waiting for the next escalation.
- The platform's side is its own note; any engine that speaks the envelope and
  knows the secret can file a report there, which was the constraint.
