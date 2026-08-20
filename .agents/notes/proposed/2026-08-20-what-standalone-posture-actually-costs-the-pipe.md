---
title: What standalone posture actually costs the pipe — 123 lines, not the weight problem
status: proposed
date: 2026-08-20
scope: hookrelay
---

## Decision

Measure before cutting. The question was how much of hookrelay's 4,226 source
lines serves a posture that is not the reference deployment, on the theory that
the judgment features are why the pipe stopped being small. **It is 123 lines,
2.9% of the source.** Removing all of them would not move the size claim, and
would cost two documented config keys, a named skip code the README promises,
and a silent behaviour change for every deployment that never wrote a `pipeline`
line.

So: **no feature is removed.** The doctrine in `hookrelay/README.md` is not the
reason the pipe is 3x its original size, and the three options below are laid out
rather than chosen, because "do we still support standalone posture at all" is a
product decision and not a tidiness one.

The measurement stands on its own regardless of which option the owner picks,
and it names one thing worth fixing that is not about posture at all (see the
duplicated mutation block, below).

## Why

**What was counted.** Source lines at `HEAD` (e4aaa02), not the working tree —
the tree was mid-edit while this was measured and moved from 4,226 to 4,354
during the session, which is its own argument for pinning a measurement to a
commit. Line spans are given so any of this can be re-checked by hand.

### Standalone-only: 123 lines, 2.9%

The doctrine names three judgment features: `filter`, `set`, and
dedup-used-as-noise-control. Everything that exists only to serve them:

| what | where | lines |
| --- | --- | --- |
| `DedupProcessor` | `processors.py:92-111` | 20 |
| `Store.recent_duplicate` (transaction + wrapper) | `store.py:133-140`, `355-357` | 11 |
| `_fingerprint_vocabulary` | `config.py:455-488` | 34 |
| `fingerprint_fields` boot validation | `config.py:388-407` | 20 |
| `fingerprint_fields` / `dedup_window_seconds` fields and parse | `config.py:81-82`, `261` | 3 |
| `FilterProcessor` | `processors.py:154-168` | 15 |
| `SetProcessor` | `processors.py:134-153` | 20 |
| | | **123** |

Two corrections to the obvious tally, both of which make the number smaller:

- **`extract.fingerprint` and the `events.fingerprint` column survive.**
  `pipeline.py:143` computes a fingerprint at record time whether or not a dedup
  stage ran, and the column is `NOT NULL`. Those 17 lines plus the index are
  ledger identity, not dedup, and would stay.
- **6 of `SetProcessor`'s 20 lines already exist twice.** `SetProcessor`'s
  mutation loop over `title`/`body`/`level`/`fields` (`processors.py:144-149`)
  appears again, byte for byte, inside `HttpProcessor` at
  `processors.py:242-247`, where it applies a brain's `set` verdict. Delete
  `SetProcessor` and that logic does not leave the codebase; it stays in the
  paired-posture stage. Net return is nearer **117**.

### Paired-only: 612 lines, 14.5% — five times larger

The honest comparison. Code that exists *only* because a brain sits behind the
relay, and which a standalone deployment never executes:

| what | where | lines |
| --- | --- | --- |
| `processed.py` — brain result to wire format | whole module | 236 |
| `actions.py` — the signed card-action token | whole module | 172 |
| `HttpProcessor` | `processors.py:170-254` | 86 |
| `Store.cold_events` + `mark_escalated` | `store.py:438-485` | 48 |
| `Store.spend_action` + outcome + `recent_actions` | `store.py:486-528` | 43 |
| `config.Escalation` | `config.py:158-184` | 27 |
| | | **612** |

**The relay carries five times more weight for the reference deployment than for
the posture it was suspected of carrying weight for.** Anyone arriving with
"cut the standalone features to get small" is pointed at the wrong 3%.

### Where the 2,810 lines actually came from

`~1400` was true on 2026-08-05 (1,416 source lines that day, and the "with
tests" in that sentence was wrong even then — tests were a separate 667). Growth
from that commit to `HEAD`, per module:

| module | 08-05 | HEAD | delta |
| --- | --- | --- | --- |
| `store.py` | 222 | 714 | +492 |
| `app.py` | 156 | 615 | +459 |
| `config.py` | 247 | 526 | +279 |
| `processed.py` | 0 | 236 | +236 |
| `channels.py` | 153 | 332 | +179 |
| `actions.py` | 0 | 172 | +172 |
| `delivery.py` | 63 | 214 | +151 |
| `templates.py` | 0 | 104 | +104 |
| everything else | | | +786 |
| **`processors.py`** | **206** | **254** | **+48** |

`processors.py` is where every judgment feature lives, and it contributed **1.7%
of the growth**. The weight arrived in the four pillars doing their own job more
thoroughly (`store`, `app`, `config`, `delivery`, `channels` = +1,560), and in
paired-posture surface (`processed` + `actions` = +408). The doctrine held. The
pipe grew by being a better pipe.

### What removal would actually cost

Config keys that stop working: `fingerprint_fields`, `dedup_window_seconds`, and
the pipeline stage types `dedup`, `set`, `filter` — five documented keys, spread
across `docs/configuration.md:36-39, 111-117, 146-163, 174` and
`config.example.yaml:23-24, 74-77`.

Tests deleted outright, ~127 lines:
`test_pipeline.py::test_duplicate_within_window_is_skipped_against_the_original`
(12) and `::test_duplicate_outside_window_passes` (6);
`test_templates.py::test_a_fingerprint_field_nothing_extracts_is_refused_at_boot`
(30) and `::test_an_enrichment_stage_before_dedup_widens_the_vocabulary` (28);
`test_extensibility.py::test_set_stage_changes_routing_outcome` (9) and
`::test_filter_stage_drops_with_named_code` (42). Plus edits to
`test_unknown_names_fail_at_boot_not_first_event` and the `conftest` fixtures.

Roughly one line of test deleted per line of source removed. That ratio is the
argument: this is not dead weight being shed, it is tested, documented,
configurable behaviour being withdrawn.

Documentation that becomes wrong, and this is the expensive part:

1. **`README.md` promise 1** — "`skipped` with a named code (`duplicate` /
   `silenced` / `no_route`)". `duplicate` ceases to exist. This is one of the
   two promises the service says it makes.
2. **The doctrine's own dedup bullet** ("dedup is CONTENT protection") and the
   posture table row whose standalone column reads `[dedup, silence, routes]`
   (+ filter/set to taste) — the column empties.
3. **`status.html:254`** — the board describes its own noise control as
   "Fingerprint dedup (`fingerprint_fields` + window)".
4. `docs/configuration.md` field reference, `config.example.yaml`, and
   `examples/stack.yaml:123` (whose comment exists precisely to tell paired
   deployments to turn dedup off).

And the operational cost nobody would see in a diff: **the default pipeline is
`[dedup, silence, routes]`.** Every deployment that never wrote a `pipeline`
block gets dedup silently withdrawn on upgrade. Duplicate suppression stops and
nothing in the ledger says why, because the gate that would have recorded
`duplicate` is gone. That is a breaking change wearing the clothes of a cleanup.

### The nuance that complicates the clean story

`set` is dual-use, and the doctrine's own table concedes it by writing
"(+ filter/set to taste)". `set: {fields: {env: prod}}` —
`docs/configuration.md:149` — adds a field so a *route* can match it. That is
the route pillar, which is pipe work by the doctrine's own test. Rewriting
`level` from `low` to `high` with the same 20 lines is judgment. One
implementation, two purposes, no way to tell them apart from config. A cut that
removes `set` removes a routing primitive to be rid of a judgment primitive.

## Consequences

**Three options, for the owner to choose between.**

- **A — keep everything, fix the sentence.** The measurement's own
  recommendation. 123 lines is not a weight problem; the README's "small" was
  written when it was true and simply never re-measured. This is now handled:
  `hookrelay/README.md` states a budget instead of a description, and
  `scripts/assert_weight.py` holds it. Cost: standalone posture keeps carrying
  three features whose doctrinal status stays "should yield", which is a
  permanent asterisk in the docs and an ongoing invitation to re-ask this
  question. Benefit: nothing breaks, no promise is withdrawn, and the 2.9%
  buys a genuinely useful posture for a team with no brain.
- **B — deprecate rather than delete.** Keep the code, mark `filter`, `set` and
  `dedup` as standalone-only in `docs/configuration.md`, and have config load
  warn when one appears in the same pipeline as an `http` stage or a brain
  channel — the combination the doctrine says should not exist. Costs ~15 new
  lines to save 0. Buys the doctrine mechanical teeth without withdrawing
  anything, and makes the eventual removal a fact about usage rather than taste.
  The middle option, and the cheapest one that changes anything.
- **C — remove them, standalone posture becomes unsupported.** Returns ~117
  source lines (2.8%), deletes ~127 test lines, withdraws five config keys and
  one of the service's two stated promises, and silently changes behaviour for
  every default-pipeline deployment. Only coherent if the answer to "do we
  support a relay with no brain behind it" becomes **no** — at which point say
  that in the README first and let the code follow. Removing the features while
  still claiming to support the posture is the worst of the three.

**Recommendation, stated but not taken: A, with B if the doctrine's asterisk
becomes annoying.** C is a product decision about who hookrelay is for, and
2.9% is not enough of a reason to make it.

**What to watch.** Two things this measurement should be re-run against:

- The `set` mutation block existing twice (`SetProcessor` and `HttpProcessor`)
  is the same duplication class as the five-copy 500 fixed on 2026-08-19. Six
  lines is small enough that extracting it may not be worth an indirection, but
  it should be a decision rather than an accident. Out of scope here — this note
  changed no source.
- The paired/standalone ratio is the number to track, not the total. It is 5:1
  today. If paired-only surface keeps growing at the rate `processed.py` and
  `actions.py` set (+408 lines in fifteen days), the interesting question stops
  being "what does standalone cost" and becomes "is the pipe absorbing the
  judgment it promised to delegate" — which the doctrine's own test would
  answer, and which `scripts/assert_weight.py` will now force somebody to ask
  out loud when the ceiling is reached.
