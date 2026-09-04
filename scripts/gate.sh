#!/usr/bin/env bash
# The whole-repo gate: run every component's OWN gate, then the stack checks
# that no component gate covers (ci-stack's docs/design/notes/locks quartet).
#
# This wrapper delegates — it never replicates a check. Each component's
# scripts/gate.sh is the exact CI replica for that component, pinned by its
# own test_gate_matches_ci contract; duplicating the list here would just be
# a second copy to drift. Two red pushes happened because a cross-component
# change ran one component's tests and nobody ran the other's gate — this is
# the single entry point that makes "before every push" one command.
#
# Usage:
#   bash scripts/gate.sh                       # all components + stack checks
#   bash scripts/gate.sh hookjudge hookrelay   # just the ones you touched
#
# CI-only (both need docker): each workflow's docker-build job, stack-smoke.
set -euo pipefail
cd "$(dirname "$0")/.."

COMPONENTS=("$@")
[ ${#COMPONENTS[@]} -gt 0 ] || COMPONENTS=(hookrelay hookjudge hookprobe)

for c in "${COMPONENTS[@]}"; do
  [ -f "$c/scripts/gate.sh" ] || { echo "unknown component: $c"; exit 2; }
  printf '\033[1m════ %s ════\033[0m\n' "$c"
  (cd "$c" && bash scripts/gate.sh)
done

printf '\033[1m════ stack ════\033[0m\n'
python3 scripts/check-docs.py
python3 scripts/assert_design.py
python3 scripts/assert_agent_notes.py
# The deployed configs' GRAPH, not their schema — assert_shadow_config.py already
# answers "does it boot". hookrelay's /topology computes the three defects a
# schema cannot see and REPORTS them, because a config reload that could be
# refused is one nobody can iterate an orchestration behind. A gate is the other
# situation: the loop that feeds a brain its own output was guarded by four
# comments in two files, which is the sign a comment was the wrong mechanism.
#
# hookrelay's venv, like the bridge lint below: this needs yaml and the pipe's
# own package, and a check that skips when an interpreter lacks them is a check
# that passes by finding nothing.
hookrelay/.venv/bin/python scripts/assert_topology.py
# scripts/assert_node_contract.py, against a REAL round from 2026-09-04 that
# posted a signal and moved neither cursor. Inverted on purpose — the checker
# must FAIL here, so this asserts the failure — because a checker that has
# quietly stopped catching anything looks exactly like one with nothing to
# catch. The companion case (the same work done correctly, twenty minutes
# earlier) and the rest live in hookrelay/tests/test_node_contract.py.
#
# The LIVE check runs in the patrol timer: it needs a before/after pair of
# runtime state, which a gate does not have. This proves the instrument still
# reads.
if hookrelay/.venv/bin/python scripts/assert_node_contract.py \
     --before scripts/fixtures/node-contract/stalled-before.json \
     --after  scripts/fixtures/node-contract/stalled-after.json \
     --ledger scripts/fixtures/node-contract/stalled-ledger.json \
     --since 1788510500 --source watch >/dev/null 2>&1; then
  echo "  FAIL  assert_node_contract.py passed a round that broke its contract" >&2
  exit 1
fi
echo "node contract: the checker still catches the round it was written for"

# This repository is public. Without .estate-identifiers this SKIPs; copy
# .estate-identifiers.example and fill it in, or set ESTATE_PATTERNS_FILE. CI
# writes the file from a repository secret with ESTATE_GUARD_REQUIRED=1, so a
# missing list fails there instead of passing quietly.
python3 scripts/assert_no_estate_identifiers.py
# Runs here and not inside a component gate on purpose: it compares three
# services' requirements.txt against their locks, and the component that forgot
# to relock is often not the component being tested. Cheap and offline — it
# resolves nothing, it only reads the two files (see the script for what that
# does and does not catch).
python3 scripts/assert_locks.py
# Also a cross-service check, and for the same reason: it weighs the pipe and
# the brain against the ceilings their own READMEs state. A component gate could
# hold its own number, but not the fact that all three are measured the same way
# and that only two of them are capped.
python3 scripts/assert_weight.py
# Cross-service for the same reason again: it compares one helper's body against
# the copy of it living in another service. `Live.watcher_count` had already gone
# missing from one of three copies, and verify_signature had stripped its
# timestamp in the pipe but not in the brain — neither noticed by anything.
python3 scripts/assert_copies.py
# Cross-service for the third time in two days: three separate places quietly held
# three opinions about which end of a list the newest row goes. /v1/audit served
# newest LAST and said so in a UI label, which made an inherited JSONL append order
# look like a decision.
python3 scripts/assert_ordering.py
# The enumerable half of doc drift. A doc audit found four statements that had
# become false and five features written down nowhere; three of the four were
# prose about behaviour, which nothing can check — the fourth was "Five kinds
# exist" over a tuple of six, and that class is this check.
python3 scripts/assert_docs.py
# The reference tables are GENERATED from settings.py and the route handlers, so a
# description cannot drift from the code that defines it. This asserts the
# committed output still matches — the same contract requirements.lock has.
python3 scripts/gen_reference.py --check
# The root shell scripts run where nobody watches — backup from cron at 04:15,
# smoke from a deploy checklist — and hookprobe's patrol.sh once shipped
# unparseable because only ITS component gate ran bash -n. Same lesson, wider
# net: a script that cannot parse must fail here, not at 04:15.
find scripts deploy -name '*.sh' -print0 | xargs -0 -r -n1 bash -n
echo "shell: every root script parses"
# The bridge was checked by NOTHING — not compiled, not linted, not typed, not
# tested — while being a live production component on the card-callback path. It
# is not inside a service package, so no component gate reaches it, and the stack
# checks had never been told to look. Same shape as the shell scripts, found the
# same way: by editing it and asking what would have caught a typo.
#
# hookrelay's venv supplies ruff because the pipe owns the other half of this
# protocol. Not guarded by an `if` — a check that skips when a tool is missing is
# a check that passes by finding nothing.
step_bridge() { printf '\n\033[1;34m── %s\033[0m\n' "$1"; }
step_bridge "the lark bridge parses and lints"
python3 -m compileall -q deploy/lark-bridge
hookrelay/.venv/bin/python -m ruff check deploy/lark-bridge
hookrelay/.venv/bin/python -m ruff format --check deploy/lark-bridge
echo "lark-bridge: OK"

# A Dockerfile is the one file no test opens. `COPY hookjudge/examples/` was
# correct read from the repo root and wrong for a build context of hookjudge/,
# and nothing local said so: the gates passed, the tag went out, and the
# release job found it eleven seconds after CI had already failed. `docker
# build --check` does NOT catch a missing COPY source — measured; it reports
# "no warnings found" on exactly this — so only a real build does.
#
# One second with a warm layer cache, and the release workflow builds these
# anyway. hookprobe is deliberately absent: its image carries Node, apt
# packages and the Claude CLI, so a cold build is minutes, and a gate people
# start skipping protects nothing. Its own CI job covers it.
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  echo "docker build (hookrelay, hookjudge)"
  docker build -q ./hookrelay >/dev/null
  docker build -q ./hookjudge >/dev/null
  echo "images build: OK"
else
  echo "docker not available — image builds skipped (CI still runs them)"
fi

printf '\033[1;32mSTACK GATE GREEN\033[0m — every component gate + the stack checks.\n'
