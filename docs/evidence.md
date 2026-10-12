# Evidence

Every number the README leans on, and whether a reader can reproduce it here.

The repository ran two deployments, both of somebody's real infrastructure. The
identifiers are scrubbed on purpose (`scripts/assert_no_estate_identifiers.py`),
and the ledgers those numbers were measured from are not in the tree — they are
runtime data, and `dataset.jsonl` is gitignored. So the honest split is not
"verified / unverified" but **two classes**:

- **Reproducible here** — a command in this repository produces the number, and
  `scripts/reproduce_claims.py` runs it.
- **Reported** — the number was measured on the retired deployments and the
  source data is not in this repository. The row names the artefact that WOULD
  back it, so a reader knows exactly what to ask for and what to distrust.

Run `python3 scripts/reproduce_claims.py` for the machine-readable version of
this table. It fails on a *reproducible* claim whose command has stopped
agreeing; it never fails on a *reported* one, because there is nothing here to
run.

| claim | stated in | artefact that backs it | class |
| --- | --- | --- | --- |
| hookrelay stays at or under 6,400 source lines | `hookrelay/README.md` | `scripts/assert_weight.py` | Reproducible |
| hookjudge stays at or under 3,400 source lines | `hookjudge/README.md` | `scripts/assert_weight.py` | Reproducible |
| every route, knob and containment row is written up or named as deliberately not | `docs/containment.md`, `*/docs/reference.md` | `scripts/assert_docs.py` | Reproducible |
| the three boards share one design language | the service pages | `scripts/assert_design.py` | Reproducible |
| a replayed signed request cannot buy a second verdict | `docs/containment.md` | `hookrelay/tests/test_signature_replay.py` | Reproducible |
| an empty `BRIDGE_INBOUND_SECRET` refuses to start | `docs/containment.md` | `deploy/lark-bridge/tests/test_fail_closed.py` | Reproducible |
| **795 production alerts; 28 of 29 rules answered identically** | `README.md`, `hookjudge/eval/README.md` | the retired deployment's `webhook_events` table — `hookjudge/eval/dataset.jsonl`, gitignored | Reported |
| **the fold sends 55% fewer cards than the rule as shipped** | `docs/deployments.md` | a replay against the retired judge ledger (`hookjudge/scripts/replay_ledger.py`), ledger not in tree | Reported |
| **the first real write happened 2026-09-30** | `README.md`, `docs/deployments.md#the-first-real-write` | the work deployment's run records and audit log | Reported |
| **`kubectl` was absent from the work image for ten days** | `docs/containment.md`, `.agents/notes/` | the release job history and the two nodes' posture records | Reported |
| **one HTTP POST from inside a probe reached the chat MCP port** | `deploy/sidecars/gate.py` docstring | the measurement on the work deployment, 2026-09-30 | Reported |

## Why the split is the point

A project whose most persuasive numbers all come from a private deployment has
two ways to be honest about it: say nothing, or say which ones a reader cannot
check. This page is the second. The alternative — presenting every number as if
it were in the tree — is the failure `docs/containment.md` names when it says a
boundary trusted for something it never claimed is the way a unit test gets read
as proof about a live model.

If you are evaluating this for real, the Reproducible rows are what you can hold
it to today. For the Reported ones, the ask is concrete: the ledger and the run
records exist, off this host, and a maintainer can export them.
