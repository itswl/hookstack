#!/usr/bin/env bash
# The whole-repo gate: run every component's OWN gate, then the stack checks
# that no component gate covers (ci-stack's docs/design/notes trio).
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

printf '\033[1;32mSTACK GATE GREEN\033[0m — every component gate + the stack checks.\n'
