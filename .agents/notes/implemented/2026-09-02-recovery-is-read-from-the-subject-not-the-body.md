---
title: The recovery sniff reads the subject line, not free-text body prose
status: implemented
date: 2026-09-02
scope: hookjudge
---

## Decision

When no explicit recovery flag is carried (`recovery_flag is None`),
`Incoming.is_recovery` now decides from the TITLE (a bracketed marker
`[RESOLVED]` / `[OK]` / `【恢复】`, an edge word, or a plain assertion the
condition ended) and from the STRUCTURED field values (Alertmanager's
`status=resolved`, carried as `fields.status` per STACK.md) — but no longer from
the free-text BODY. The loose `"ok" in words` catch-all is gone; `OK` now counts
only in a bracket/edge marker, where a real `[OK]` / `... OK` recovery lives.
`_asserts_recovery` also gained a `失败`/`失效` guard so `恢复失败` (recovery
FAILED) is not read as a recovery.

## Why

The recovery route skips the model and the pipe renders a green, button-less
card, so a false positive reports a LIVE incident as resolved. The old sniff
scanned title + body + fields with keyword containment. Executed against the
shipping code, all of these came back `is_recovery=True` incorrectly:

- `"Payment probe not OK for 5m"` — `OK` mid-sentence via `"ok" in words`.
- body `"…resolved to 10.0.0.1…"` — `\bresolved\b` in DNS prose.
- `"status != OK for 10m"` — `OK` again.
- `"自动恢复失败"` — the recovery failed; `恢复` matched with no suffix guard.

Recovery is a fact monitoring systems state in the subject line or in an
explicit field (which is why `fields.status` is still read — the Alertmanager
resolve carries the fact nowhere else), not something buried in body prose. A
pipe that knows the fact sets `recovery_flag`, which still wins outright. Production is unaffected: both
live sources in `deploy/shadow.yaml` (`ww`, `judge-notify`) carry
`recovery: "{meta.is_recovery}"`, so the flag path runs and the sniff is the
flagless fallback only. The change is what hardens that fallback for any
deployment whose upstream does not state the fact.

## Consequences

- All four false positives above now read as firings; existing recovery tests
  (bracketed pairs, Chinese markers, `status: OK`, the negation set) still pass.
  New cases are pinned in `tests/test_judge.py`.
- A deployment whose upstream states recovery ONLY in the body, with no flag and
  nothing in the title, will now miss it and re-judge the resolution as a fresh
  firing (costs a verdict; never a false green). The fix for that is to carry
  the state as a field/flag, which the pipe already supports and STACK.md already
  documents.
- Identity/`condition_title` are unchanged — they use the same markers to strip
  decoration, which this does not touch.
