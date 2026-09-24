---
title: Three boundaries the successor project found, that this stack still had open — the withheld secrets, the verifier's git, and the swallowed handshake
status: implemented
date: 2026-09-24
scope: stack
---

## Decision

Three fixes, each one a defect found while building a separate project
(`airlock`, a from-scratch take on the same problem) and then looked for HERE,
where the same code or the same shape was still live. The operator's direction
is that hookstack stays the main line, so what that project learned comes back
as patches rather than as a migration.

1. **The secrets withheld from the agent are withheld in fact.** The service
   now calls `gate.withhold_this_process_from_the_agent()` at boot
   (`prctl(PR_SET_DUMPABLE, 0)`), which makes its `/proc` entry root-owned and
   refuses a same-uid reader. The result is logged as one line, because "the
   agent cannot see the signing keys" should be checkable in a log.
2. **The patch verifier cannot be made to run the clone's code.**
   `patches._git` runs with `--no-ext-diff --no-textconv --no-pager`,
   `core.fsmonitor=false`, `core.hooksPath=/dev/null`, and without the system
   and global config files.
3. **The egress proxy no longer swallows bytes sent behind `CONNECT`.**
   `Handler.rbufsize = 0`.

## Why

**1. `SECRETS_WITHHELD_FROM_AGENT` was a boundary the agent could walk around.**
The list and its own note are right about what the agent must not have: the
pipe's HMAC keys forge a signed event, the chat credentials post as the bot.
`gate.environment` blanks all of them. But the agent is a CHILD of the service
and runs under the SAME uid, and on Linux that means `/proc/<service>/environ` —
the values as they are, not as they were blanked.

Measured on hookprobe's own image, not argued:

```
before: True      # sh -c 'cat /proc/1/environ' finds HOOKPROBE_EVENT_SECRET
verdict: on: a process of this uid cannot read this service's environment or memory
after:  False
```

`sh -c` is the shape of every Bash tool call, so this was reachable by one
injected instruction in an alert body — the same threat the withholding was
written for, arriving by the door beside the one that was locked. The fix is the
kernel's, not a list's: with `PR_SET_DUMPABLE` cleared the read is refused
before any pattern gets a say.

What it does not do, stated because the log line will otherwise be read as more
than it is: it protects THIS process only (the flag is not inherited across
`exec`), it does nothing for a secret that reaches the container as a FILE, and
**as root it is a no-op** — hence the `OFF:` branch, which says so rather than
reporting success. The image runs as uid 10001, so the shipping case is covered.

**2. The verifier read a repository the agent had spent the whole run writing
to.** `patches.verify` runs `git log` and `git diff` inside `/data/code/<repo>`
to check the agent's claimed diff against the clone's commits. `.git/config` and
`.gitattributes` are files in that clone, and several git settings name a
program git then executes: `diff.external` on every `git diff`, a `textconv`
driver on the paths an attribute selects. Either one runs HERE, in the service
process — which holds the keys the agent's own environment is blanked of. The
check written to catch a lying agent would have been the mechanism that ran its
code.

Planted in a clone's own config and measured: a plain `git diff` runs both, the
hardened invocation runs neither, and the diff it returns is byte-identical to
the same commits' diff in a clean repository.

**The first attempt at this was wrong in a way worth recording.** Overriding the
settings to empty (`-c diff.external=`) does not disable the feature — git reads
it as "run the empty string" and dies with `external diff died, stopping at
README.md` on the first file. The existing verify tests went red, which is how
it was caught. The flags are what turn these off; only `core.fsmonitor` has a
disabling value. A hardening that breaks the thing it hardens is the failure
mode this repository has hit before, and it was caught here by tests that
already existed.

**3. The proxy defect was found in the copy, and the original still had it.**
`StreamRequestHandler` buffers `rfile`, so the first `readline` takes whatever
arrived with the request line off the socket — including a ClientHello from a
client that starts TLS without waiting for the 200. The tunnel then forwards
from the raw socket and the handshake never arrives. It reads like a slow
upstream, which is exactly how it presented.

This is the reason the airlock work is worth anything to this repository at all:
`deploy/egress-proxy/proxy.py` and its twin are the same code, so a bug found in
one is a bug in the other, and nothing propagates it but somebody looking.

**Why these three and not a port of the design.** The successor's larger ideas —
an approval bound to a plan hash, a launcher that re-checks, one container per
step group — are about a door this stack has never opened:
`HOOKPROBE_REMEDIATION_ALLOWLIST` is still empty, and
[[three-gaps-before-the-execution-door-opens]] already made the argument that
hardening an unopened door is the wrong order. These three are different: all
three are live paths, two of them on the work deployment as it runs today.

## Consequences

* **Each fix has a test that fails without it**, checked by removing the fix and
  watching the test go red — the proxy's (`test_bytes_sent_right_behind_the_connect_reach_the_far_side`),
  the verifier's (`test_the_clone_cannot_make_the_verifier_run_its_code`), and
  the readback's (`test_the_withheld_secrets_cannot_be_read_back_out_of_the_service`).
  The two markers the verifier test plants are a `touch` in pytest's tmp
  directory: harmless if a regression lets them run, which is the only reason
  they can be asserted on.
* **The readback test skips on macOS**, where there is no `/proc` and the hole
  does not exist. That makes the local gate silent about the fix that matters
  most, so the proof is the container run quoted above, against hookprobe's own
  image and this branch's source. Re-run it after touching `gate.py`:
  `docker run --rm --entrypoint python -e HOOKPROBE_EVENT_SECRET=x -v $PWD/hookprobe:/src:ro -w /src hookprobe:work -c '...'`
* **`/proc/self/*` becomes unreadable to the service itself.** `os.environ` is
  already in memory and unaffected, and fd listing still works; anything added
  later that reads its own `/proc/self/environ` will fail, and this is where to
  look when it does. Core dumps are disabled with it.
* **Nothing here is deployed.** The work stack has been down since the laptop
  moved to the successor project, and the production deployment is retired.
  These land on a branch; whoever brings the work stack back reads the log line
  for `agent readback:` as the first check that the fix arrived — the
  point-of-consumption rule, which is how the last two knobs in this repository
  turned out not to have arrived at all.
* **The remaining items from that comparison are not here**, and are listed in
  the successor's discussion #7 rather than re-argued in this repository: the
  watcher's signing key living in the agent's own reach, the chat MCP's writable
  credential sitting behind a tool list instead of a gateway, and the handoff
  being prose rather than a version and a hash. Each is a design change with its
  own note to write, and each needs the work stack running to verify.
