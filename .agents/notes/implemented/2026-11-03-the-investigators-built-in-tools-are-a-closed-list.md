---
title: The investigator's built-in tools are a closed list, because a CLI upgrade moved them unreviewed
status: implemented
date: 2026-11-03
scope: hookprobe
---

## Decision

The Claude engine passes the SDK a base set of built-in tools (`tools`), and
the list is closed: Bash, Read, Write, Edit, Glob, Grep, WebSearch, WebFetch,
Skill, Agent, and the four task-list tools that replaced TodoWrite. The CLI
offers nothing else, in a run or in its subagents. MCP tools are untouched;
`HOOKPROBE_MCP_TOOLS` and the MCP guard already decide those. ToolSearch is
left out, so nothing is deferred and every listed tool is offered directly.

## Why

A second deployment running copies of these files adds each new command its
upstream ships to its deny list as soon as an upgrade brings one. Asked here,
the same question had no answer. Under `bypassPermissions` the engine's
`allowed_tools` restricts nothing, so a run could reach whatever the bundled CLI
shipped, and the CLI moves with every relock of the SDK. Measured with a
recording stand-in model on the published images: CLI 2.1.235 (0.9.2) offered
26 tools, and CLI 2.1.316 (0.10.0) offered 14 directly and 15 more behind
ToolSearch. Monitor, ListAgents and ToolSearch were new, and TaskOutput was
gone. None of it had been reviewed.

Monitor runs shell commands. Loaded through ToolSearch, its write into
`results/` was still refused, because the CLI runs the Bash hook for it. That
held by the CLI's choice, not by anything in this repository, and the bash
guard's own matcher only names Bash. A closed list turns "the next tool will
probably be covered" into "the next tool is not offered until someone adds it
here".

Measured on this change's image with a one-tool MCP server: exactly the listed
tools and the MCP tool are offered, nothing is deferred, the MCP tool answers,
and Monitor and ToolSearch are refused as tools that do not exist.

## Consequences

- A tool a later CLI adds stays out until it is added to `_TOOLS` and to the
  test that pins it, on purpose.
- The CLI ignores a name it does not know. A tool that a later CLI renames
  therefore drops out silently, and an SDK bump has to be checked against what
  the new CLI offers. It still accepts `Task` as the old name for `Agent`.
- Checked at SDK 0.2.181 (CLI 2.1.323, 2026-11-13): the request now carries a
  fifteenth entry beside the fourteen, `DeferredToolPlaceholder`, marked
  `defer_loading` and described as "Reserved placeholder that keeps deferred
  tool loading active; never call this tool". It is not a capability: a
  stand-in model scripted to call it got `No such tool available:
  DeferredToolPlaceholder` back. The list stays fourteen.
- An operator who wants a tool off the list, such as NotebookEdit or a
  scheduler, adds it in the engine. There is no setting for it, on purpose.
- Not changed: the console's live plan still reads TodoWrite calls, which no
  current CLI offers. The task-list tools that replaced it show as ordinary
  steps.
- The codex and pi runtimes have their own tool models and are not covered by
  this list.
