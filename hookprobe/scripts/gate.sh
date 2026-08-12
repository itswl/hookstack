#!/usr/bin/env bash
# The full gate — an exact replica of the CI job. Run before every push.
# A local list that is "close enough" is how a red CI arrives as a surprise.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PY:-.venv/bin/python}
step() { printf '\n\033[1;34m── %s\033[0m\n' "$1"; }

step "compileall (syntax, every file)"
$PY -m compileall -q hookprobe tests

step "ruff check"
$PY -m ruff check hookprobe tests

step "ruff format --check"
$PY -m ruff format --check hookprobe tests

step "bandit (static security scan)"
$PY -m bandit -q -r hookprobe

step "sessions page inline JS parses"
node -e '
const fs = require("fs");
const m = fs.readFileSync("hookprobe/ui.html", "utf8").match(/<script>([\s\S]*)<\/script>/);
if (!m) { console.error("no inline script in ui.html"); process.exit(1); }
new Function(m[1]);
console.log("ui.html inline JS: OK");
'

step "pytest"
$PY -m pytest -q

step "pip-audit (known-vulnerable dependencies)"
$PY -m pip_audit --progress-spinner off

printf '\n\033[1;32mGATE GREEN\033[0m — matches the CI job.\n'
