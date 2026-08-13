---
title: Any member returning results to the pipe speaks the processed-event dialect
status: implemented
date: 2026-08-12
scope: stack
---

## Decision

A family member that returns results through hookrelay's channels must put
`meta.alert_name`, `analysis.summary` and `meta.importance` in its payload. That is
the shape `hookrelay/processed.py` renders, and it is the contract for the return
door, not an internal detail of hookjudge.

hookprobe's investigation reports therefore carry brain-dialect fields alongside
their own (`report.summary`, `report.text`), rather than only their own.

## Why

The investigator's first live return looked correct everywhere it was easy to look.
The relay's ledger showed the delivery, the `probe-notify` source template extracted
its fields, and the HTTP status was 200. What actually arrived in the chat channel
was an empty card: `payload: processed` channels read the three fields above, found
nothing, and rendered a shell. Silently — an empty card is a successful delivery.

The pipe is content-blind by design, so it cannot warn about this. The renderer's
inputs are the contract, and a second brain joining the loop has to speak it.

## Consequences

- Verification of any new returning member checks the **rendered card**, not the
  ledger row and not the HTTP status. The ledger will look fine.
- `hookrelay/examples/stack.yaml`'s `probe-notify` source reads
  `{meta.alert_name}` / `{analysis.summary}` / `{meta.importance}`, which is the
  same contract stated once more in configuration.
- Adding a field to the renderer means updating every member that returns results;
  the dialect is shared, so it is a stack-scoped change, never a local one.
