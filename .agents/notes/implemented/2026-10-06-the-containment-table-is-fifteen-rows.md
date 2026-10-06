---
title: The containment table is fifteen rows
status: implemented
date: 2026-10-06
scope: stack
---

## Decision

`docs/containment.md` regroups its table from twenty-nine rows to fifteen by
mechanism, keeping every claim and every limit in the third column:

- the three signatures (the pipe's doors, a card's buttons, the bridge's
  inbound) are one row, because they are one mechanism with one residual (a
  replay inside the window, and an empty secret);
- per-node credentials and the agent's read-only bearer are one row: the
  credential is the real boundary and the bearer is its shape inside a node;
- the declared bypass lanes, the verdict vocabulary and the MCP tool allowlist
  are one row, "closed sets at the doors", with the socket limit that led to
  the gate row kept word for word;
- the budget breaker and the retry caps are one row of arithmetic;
- the remediation allowlist, the allowlisted execution environment and the
  declared blast radius are one row: what an approved procedure may run, and
  with what;
- the approval window and the freshness cursor are one row: a clock and a
  cursor the report cannot move, each naming the gap the other closes;
- the bash guard and the input guard are one row of regexes over model text;
- the high-risk allowlist and the target cooldown are one row keyed on fields
  the model writes;
- the four config-load invariants are one row.

The chat gate, the watch signer, tool gate reachability, the chat sender
allowlist, the chained audit and the memory shape check stay as they were.

## Why

Fourth of the four cuts. The page's own first paragraph says the question an
operator has — if the model is hostile, which of these is still standing? — is
not answered by reading a long list, and the list had grown by one row per
mechanism added rather than per kind of failure. Three signature rows said the
same residual three times; three closed-set rows said "a wrong choice inside a
declared set" three times. Fifteen rows is one row per argument. Nothing was
dropped: the measured dates and the incidents are in the merged cells.

## Consequences

- The four classification lists under "What holds, and what only helps" name
  the fifteen rows; the gate's check that every row is sorted still runs.
- A new mechanism joins the row whose argument it shares, or starts a row if
  it is a new kind of failure. "One mechanism, one row" is no longer the rule.
- Prose elsewhere that named a merged row by its old name was reworded in the
  same change.
