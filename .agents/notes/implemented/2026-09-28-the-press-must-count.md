---
title: The press must count — who approved is a field, a clean run is not a witness, and a ruling is read back from the judge
status: implemented
date: 2026-09-28
scope: stack
---

## Decision

Three things a person's press has to do, each now checked where it lands
rather than where it was sent:

1. **An approval names its actor as a field.** `remediation.approve` takes
   `actor`; the card door passes the IM user id it always had, the console
   passes `{by: …}` or says `console`. The row carries `approved_by`, the
   automation ledger's `approved` line carries `actor`, the board's procedure
   artifact reads "approved by …". The free-text note stays as it was.
2. **A clean run verifies nothing; the condition does.** The contract of
   [[execution-success-is-not-recovery]], landed: an executed procedure is
   `verifying` until the pipe says what the condition did next, `held` on a
   recovery (or on a window that closed quietly, in weaker words), and
   `did_not_hold` on a re-fire inside the window. `verified_by: remediation`
   now means held.
3. **A ruling on a verdict is read back from the judge's ledger.** The stack
   smoke presses "Worth waking me" on the first judge card through the pipe's
   own door and then asks the judge's `/status` for a row with
   `mattered: yes` by that actor. The pipe's "forwarded" is not the check; the
   ledger is.

## Why

Pilot zero ([[pilot-zero-read-back-and-the-order-of-the-next-ninety-days]]):
the seven rulings a person gave on 2026-08-21 were recorded by the pipe as
forwarded and never appeared in the judge's ledger; the archive's judge
database holds no row for any of the five correlation ids the presses carried,
and no rows at all for that day, so the mechanism cannot be read back now — the
class can be: a value computed correctly and dropped on the way to where it
acts, which `AGENTS.md` names as this repository's recurring bug. Approvals were
anonymous on the console and free-text on the card; the north star counted
every procedure that exited 0 as verified, so a fix that fixed nothing read the
same as one that worked.

The first two are code; the third is a check, and the one that matters most:
it is the first assertion in this repository that reads a person's press back
from the ledger it was meant to reach.

## Consequences

`closed_unattended` and `verified` drop on any deployment whose alerts do not
resolve through the pipe and whose window has not closed; that is the honest
number. `HOOKPROBE_REMEDIATION_VERIFY_SECONDS` is passed by the production and
source composes; the quickstart's recovery arrives seconds after execution, so
the demo shows `held` by recovery. The console's `by` is a name the operator
types into the API, not an identity — the shared bearer is still the shared
bearer; a real identity waits on the operator's console decision.

## Rejected

- **Counting a window that closed quietly as unverified.** The 08-31 note
  chose "held, in the weakest words", and it stands: a procedure the alert
  never contradicted is not the same as one nobody watched, and a board that
  never lets an unattended deployment close anything is one nobody reads.
- **Letting a later recovery overwrite a `did_not_hold`.** The first answer
  stands; a condition that fired again and then cleared is a flapping
  condition, and the procedure did not hold it.
