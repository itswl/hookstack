---
title: The lock pins the agent runtime, and the image stops installing a second CLI
status: implemented
date: 2026-11-02
scope: hookprobe
---

## Decision

The investigator's image no longer installs the Claude Code CLI from npm, and
the `CLAUDE_CODE_VERSION` build argument is gone. The CLI that runs an
investigation is the one inside the claude-agent-sdk wheel, so the
`claude-agent-sdk==` line in `hookprobe/requirements.lock` is the agent-runtime
pin, and Dependabot proposes it now that its strategy for this service raises
floors. Node stays, for MCP servers started with npx.

## Why

The SDK runs its bundled binary before it looks at PATH, and nothing here sets
`cli_path`. Read inside the images: the published 0.9.2 investigator ran CLI
2.1.235, bundled with SDK 0.2.140, while the npm CLI pinned at 2.1.259 sat on
PATH and never ran. After the relock to SDK 0.2.176 the engine runs 2.1.316.
The pin this Dockerfile documented had decided nothing since the SDK began
bundling the CLI, and it was still read as the answer: the 2026-09-03 fix for a
gateway's model ids raised it and changed nothing that ran.

The other way out was to point the engine at the pinned npm CLI with
`cli_path`. That keeps a version Dependabot cannot see, runs a CLI older than
the one each SDK release was built and tested with, and keeps two pins that
have to agree. The operator chose the lock.

## Consequences

- A relock of claude-agent-sdk is a CLI bump. The SDK's changelog names the
  CLI each release bundles, and `claude_agent_sdk/_cli_version.py` names it
  inside an image.
- `claude` is no longer on PATH in the image, so `claude --version` there
  fails; read the version file instead.
- The image lost the npm CLI: 1.69 GB before and 1.17 GB after, in a local
  build of the same lock.
- An operator who needs a CLI release the SDK has not shipped yet has no build
  argument any more. `ClaudeAgentOptions.cli_path` is where that would go, and
  it stays unwired until someone needs it.
- Check every SDK bump against a gateway's own model ids: a CLI release once
  refused them with `[claude-code:unrecognized_model]`. 2.1.235 and 2.1.316
  both accept them.
