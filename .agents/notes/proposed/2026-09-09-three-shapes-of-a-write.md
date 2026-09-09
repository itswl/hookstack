---
title: Three shapes of a write, and the two the input guard could not see
status: proposed
date: 2026-09-09
scope: hookprobe
---

## Decision

The input guard now checks three shapes instead of one, and `AGENTS.md` joins
the protected list. Found by doing the thing that had been listed as an
unproven claim: watching the guards actually refuse on codex and pi, rather
than trusting that they would.

They did not. Two of the three shapes went straight through, and one of those
had been going through on **every** runtime since the guard was written.

## Why

The guard's sentence was "writes to the files that steer the next run are
refused". What it actually implemented was "a `Write` or `Edit` tool call whose
`file_path` argument names a protected path is refused", which is a much
smaller claim wearing the same words.

**Shape one, the one it saw.** `Edit` with `{"path": "…/CLAUDE.md"}`. This is
what pi sends and what Claude sends. Worked all along.

**Shape two: a patch envelope.** Codex has no edit tool with a path argument. It
sends one `apply_patch` call whose entire patch is a string, and the paths are
lines inside it:

```
tool_name  = "apply_patch"
tool_input = {"command": "*** Begin Patch\n*** Update File: /data/CLAUDE.md\n@@ …"}
```

A guard reading argument keys saw an unrecognised tool carrying no path, and let
it through. `gate.patch_targets` now reads the envelope.

**Shape three: the shell.** `printf 'x' >> CLAUDE.md` is a Bash call. Bash has
no path argument, so the input guard never looked; and the bash guard permits it
under both `readonly` and `danger-only`, because writing a file in your own
workspace is not on its list of dangerous verbs. **Nothing anywhere was looking
at where those bytes went.** That was true on Claude, codex and pi alike, and it
had been true for the whole life of the guard.

`gate.shell_write_target` now splits a command on the shell's own separators and
checks two things per segment: a redirect or `tee` target, and — for `sed -i`,
`cp`, `mv`, `install`, `dd` — every argument. The Claude adapter's in-process
bash hook asks the same question, through the same function, so this did not
become a fourth posture.

**And `AGENTS.md` was not protected at all.** The list knew `CLAUDE.md`. Codex
and pi both read `AGENTS.md` as the standing instructions for the next run, so
on those runtimes the guard was protecting a file the model did not use and
ignoring the one it did.

## What was measured

The tool names came from real turns, not from documentation:

| | codex | pi |
| --- | --- | --- |
| MCP call | `mcp__probe__probe_echo` | no MCP surface |
| file edit | `apply_patch`, path inside the patch | `Edit` with `path` |
| what the model reached for first | the edit tool | **a shell redirect** |

That last row is worth keeping. Asked to append a line to `CLAUDE.md`, pi did
not use its edit tool. It ran `printf '%s\n' 'probe-touched' >> CLAUDE.md`,
which at the time nothing stopped. The hole was not hypothetical and it was not
hard to reach: the model picked it unprompted, on the first attempt.

Then, after the fix, on both runtimes with `danger-only` (so the sandbox
permits writes and only the guard stands between):

```
codex  mcp    mcp__probe__probe_echo
       input  apply_patch          …/CLAUDE.md
       input  Bash                 printf 'mine\n' >> AGENTS.md
pi     input  Edit                 CLAUDE.md
       input  Bash                 printf 'mine\n' >> AGENTS.md
```

Both files unchanged afterwards, and both agents reported the refusals in their
own words.

## Consequences

**The containment row now says what it does and does not stop**, in all three
shapes, and names that the shell check catches the obvious forms and not a
redirect built at runtime out of two variables. That is the same honesty the
bash guard's row already had, and it is the right amount: this stops the
over-eager model, which is the case that happens.

**A claim listed as unproven turned out to be three defects.** The entry said
"the documentation says they will fire; that is not the same as having seen
it". Both halves of that were worth acting on, and the cost was two turns.

**`danger-only` deserves a second look.** Everything here was found with the
sandbox permitting writes, which is what that posture means. Under `readonly`
the kernel refuses first and these holes are invisible — which is exactly why
they survived so long.
