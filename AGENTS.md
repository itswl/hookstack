# Working on this repository as an agent

Rules that exist because each was violated once and cost something. The
enforcement lives in `scripts/` and the tests; this file is the map, not the
law — where the two disagree, the checks are right.

## The gate is read, never chained

Run `bash scripts/gate.sh` as its own step and READ the verdict before any
commit or push. A chained `gate.sh; git commit && git push` once shipped a red
gate to CI and production in one line: `&&` reads exit codes, not reports.
Related trap, same day: `git checkout <file>` restores the COMMITTED version
and silently destroys uncommitted edits — twice.

## Deploying

Only via `scripts/deploy.sh` on the deploy host. It holds the knowledge that
failed twice from memory: compose resolves `.env` relative to the compose
FILE's directory (so `--env-file` must be spelled out), the `-p` project names
are load-bearing (a different one starts a second copy of production beside
production), `--build` is not optional, and the golden replay
(`hookjudge/scripts/eval.py --gate`) runs between build and up — a prompt
change that under-calls a golden incident does not ship. `SKIP_EVAL=1` is the
recorded emergency hatch.

`/srv/hookstack` anywhere in this repository is a PLACEHOLDER, not a path.
The real root is pinned host-side via `HOOKSTACK_ROOT` in the crontab lines;
treating the placeholder as live once broke the nightly backup.

## Estate identifiers

Real hostnames, IPs, deploy paths, company and rule names never enter tracked
files. `scripts/assert_no_estate_identifiers.py` enforces it in the gate and
in CI, from a pattern list that is deliberately NOT in this repository
(`.estate-identifiers`, git-ignored; format in the `.example`). Ask the
operator for the master list — a three-rule improvisation passes the guard
while leaking everything the other rules cover. The guard reads `git ls-files`
only, so ignore rules are the backstop for files that must never be tracked.

## More than one agent works here

Sessions have collided twice: full-history rewrites for privacy scrubs, and
racing deploys. Before history surgery or a deploy, check for peer sessions
and say what you are about to do. On finding a rewritten remote: verify your
content survived, cherry-pick unpushed work onto the rewrite, never
force-push. The `patrols/` directory on the deploy host is deploy-local and
untracked — coordinate ownership before editing it.

## Verify at the point of consumption

The recurring bug shape here is a value computed correctly and dropped on the
way to where it acts: a wake answer that never reached the return payload, a
subagent model knob the CLI silently rejected while a deletion clock ran on
"unused". Verifying at the point of production proves nothing — read the
delivered payload, the audit log, the board. And a number that decides a
deletion gets audited before it executes.

## Decisions are written down

`.agents/notes/` holds them — proposed, implemented, and especially rejected
(`assert_agent_notes.py` checks the shape). Read the relevant note before
re-deciding something; write one when you decide something a diff cannot
explain. Line ceilings (`assert_weight.py`) are features: raise them
deliberately, in both stated homes, with the reason — never trim docstrings to
fit, they are the decision records.
