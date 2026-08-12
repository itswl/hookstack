#!/usr/bin/env bash
# The full gate — an exact replica of the CI job. Run before every push.
# A local list that is "close enough" is how a red CI arrives as a surprise.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PY:-.venv/bin/python}
step() { printf '\n\033[1;34m── %s\033[0m\n' "$1"; }

step "compileall (syntax, every file)"
$PY -m compileall -q hookjudge tests

step "ruff check"
$PY -m ruff check hookjudge tests

step "ruff format --check"
$PY -m ruff format --check hookjudge tests

step "bandit (static security scan)"
$PY -m bandit -q -r hookjudge

step "inline page JS parses"
node -e '
const fs = require("fs");
const m = fs.readFileSync("hookjudge/status.html", "utf8").match(/<script>([\s\S]*)<\/script>/);
if (!m) { console.error("no inline script in status.html"); process.exit(1); }
new Function(m[1]);
console.log("status.html inline JS: OK");
'

step "pytest"
$PY -m pytest -q

step "pip-audit (known-vulnerable dependencies)"
$PY -m pip_audit --progress-spinner off

printf '\n\033[1;32mGATE GREEN\033[0m — matches the CI job.\n'
