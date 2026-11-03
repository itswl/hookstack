---
title: The files configuration points at steer every run too, and the mount is what holds
status: implemented
date: 2026-11-03
scope: hookprobe
---

## Decision

The input guard protects every file the operator names by path, wherever it
lives: the MCP config, the roles file, both remediation allowlists, the
declared radius, the decline patterns, and the codex and pi configs. It also
protects the appended methodology when its path is configured. A write to one is
refused on all three runtimes, and the before-and-after digest of a run covers
them. At startup the posture record names any of the operator's files this
node's own user could write or replace (`writable_inputs`), and the alarm
channel says so once. The work deployment's planner and watcher read their MCP
config from their read-only secrets mounts instead of their volumes, and the
configuration doc no longer offers the volume as a place for it.

## Why

A second deployment running copies of these files audited what its agent could
write, and found its own deny list and scheduled jobs on a writable volume.
Asked here, the same question found the MCP config. The work deployment kept it
on each node's volume, the engine rereads it at every run and starts the stdio
servers it names, and the input guard protected only fixed names in the
workdir. Measured on the probe image with that setting and a recording stand-in
for the model API: one run wrote the file through an ordinary shell step, and
the next run, which made no tool call at all, started the planted command. No
guard and no audit line saw it.

The guard alone is not a boundary. The agent shares the service's UID, and the
bash guard's docstring already says a determined shell gets past a regex. For
the workdir's files that is accepted, because the service writes them for the
operator and a mount cannot tell the two apart. These files are different: no
endpoint writes them, so a read-only mount can hold, and checking whether one is
in place is cheap.

Reported rather than refused: refusing to start would stop a node that ran
yesterday with a config an older version of the docs told its operator to put
on the volume.

## Consequences

- A run asked to edit its MCP config, roles or allowlist is refused, and told
  that an operator changes it outside any run.
- A configured path on the volume keeps working. It is named in
  `writable_inputs` and paged once per start until it moves to a read-only
  mount.
- The work deployment's two MCP configs moved from each node's volume to its
  read-only secrets directory. They are still edited on the host and read
  fresh by the next run.
- A new file setting has to be classified on purpose: `tests/test_inputs.py`
  fails while one is unclassified.
- Not changed: codex keeps its hooks in a home on the run's volume, which is
  already a containment row of its own.
