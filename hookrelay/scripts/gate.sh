#!/usr/bin/env bash
# The full gate — an exact replica of the CI job.
#
# Run this before every push. It is deliberately the SAME list CI runs, in the
# same order: a local list that is "close enough" is how a red CI arrives as a
# surprise. When a check is added to ci.yml, add it here in the same change
# (and vice versa) — the contract test test_gate_matches_ci pins that.
set -euo pipefail
cd "$(dirname "$0")/.."

PY=${PY:-.venv/bin/python}

step() { printf '\n\033[1;34m── %s\033[0m\n' "$1"; }

# Every tool this gate runs is pinned in requirements.txt, so a missing venv
# means the gate silently degrades into "command not found" — or worse, picks
# up whatever versions happen to be on PATH and disagrees with CI. Say what to
# do instead of failing on a bare path.
if [ ! -x "$PY" ]; then
  printf '\033[1;31mno interpreter at %s\033[0m\n\n' "$PY"
  printf 'One-time setup for this service:\n\n'
  printf '  python3 -m venv .venv\n'
  printf '  .venv/bin/python -m pip install -r requirements.txt\n\n'
  printf 'Or point the gate at your own: PY=/path/to/python bash scripts/gate.sh\n'
  exit 1
fi

step "compileall (syntax, every file)"
$PY -m compileall -q hookrelay tests

step "ruff check"
$PY -m ruff check hookrelay tests

step "ruff format --check"
$PY -m ruff format --check hookrelay tests

step "mypy (types)"
$PY -m mypy hookrelay

step "bandit (static security scan)"
$PY -m bandit -q -r hookrelay

step "inline page JS parses"
node -e '
const fs = require("fs");
const html = fs.readFileSync("hookrelay/status.html", "utf8");
const m = html.match(/<script>([\s\S]*)<\/script>/);
if (!m) { console.error("no inline script found in status.html"); process.exit(1); }
new Function(m[1]);            // parse only, never execute
console.log("status.html inline JS: OK");
'

step "example plugins import cleanly"
$PY - <<'PYEOF'
from pathlib import Path

from hookrelay import registry

loaded = registry.load_plugins(Path("examples/plugins"))
assert loaded, "examples/plugins loaded nothing — an untested example is a lie"
print("plugins:", ", ".join(loaded))
PYEOF

step "config.example.yaml is valid"
$PY - <<'PYEOF'
import os

os.environ.setdefault("FEISHU_WEBHOOK_URL", "https://example.invalid/hook")
os.environ.setdefault("DINGTALK_WEBHOOK_URL", "https://example.invalid/hook")
os.environ.setdefault("WECOM_WEBHOOK_URL", "https://example.invalid/hook")
os.environ.setdefault("ARCHIVE_WEBHOOK_URL", "https://example.invalid/hook")

from hookrelay.config import Config

cfg = Config.from_file("config.example.yaml")
print(f"example config: {len(cfg.sources)} sources, {len(cfg.channels)} channels, {len(cfg.routes)} routes")
PYEOF

step "pytest"
$PY -m pytest -q

step "pip-audit (known-vulnerable dependencies)"
$PY -m pip_audit --progress-spinner off

printf '\n\033[1;32mGATE GREEN\033[0m — matches the CI job.\n'
