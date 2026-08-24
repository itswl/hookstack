Read AGENTS.md before changing anything — every rule in it was paid for once.

The short version: run `bash scripts/gate.sh` and READ its verdict before any
commit; deploy only via `scripts/deploy.sh`; `/srv/hookstack` is a placeholder,
never a path; real estate identifiers never enter tracked files; more than one
agent works this repo — check for peers before history surgery or deploys.
