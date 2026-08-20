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

printf '\033[1;32mSTACK GATE GREEN\033[0m — every component gate + the stack checks.\n'
