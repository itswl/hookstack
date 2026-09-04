---
title: Mounting an MCP server does not grant its tools
status: implemented
date: 2026-09-04
scope: hookprobe
---

## Decision

`HOOKPROBE_MCP_TOOLS` names the closed set of MCP tools an instance may call —
`mcp__chat__chat_search_messages`, or `mcp__chat__*` for a whole server.
**Empty denies every MCP tool**, so mounting a server through
`HOOKPROBE_MCP_CONFIG` grants nothing by itself. Enforced by a PreToolUse hook
(`_mcp_guard_hook`), the same mechanism as the bash guard, and the refusal names
what the instance *may* call.

## Why

There is no such thing as a read-only MCP server. a chat server ships
`chat_send_message`, `chat_create_todo`, `chat_delete_todos` and
`chat_manage_group_members` in the same server as `chat_search_messages`.
Before this, "give the planner eyes on the thread a signal came from" and "let
anything written in that thread post messages as the operator" were one mount
and one decision.

That matters here more than it would elsewhere, because this is the component
that reads attacker-influenced text by design — a work signal is a colleague's
own words, forwarded. Whoever can put a sentence in front of this agent can ask
it to use every tool it holds. So plumbing (which server is reachable) and
policy (what may be done with it) are deliberately two settings, and the second
one is closed until somebody writes the list.

A hook rather than `allowed_tools` because the engine runs
`permission_mode="bypassPermissions"`, where an allowlist is a preference.
MEASURED, not assumed: with the tool absent from `_ALLOWED_TOOLS` and no hook,
a call still went through — `allowed_tools` did not gate it. Verified on
2026-09-04 against a live server: `mcp__chat__chat_get_me` succeeded and
`mcp__chat__chat_send_message` was refused with the reason text, in one run.

Closed-when-unconfigured follows the family's existing posture for security
doors (hookjudge's `/rulings/ai` returns 503 rather than accepting unsigned).
The blast radius here is larger than a ledger row, so it gets the stricter
default even though it is a behaviour change for anyone who had mounted a
server and relied on it — and the denial text names the knob, so that lands as
a readable error instead of a silent loss.

## Consequences

- The declared tools are also appended to the SDK's `allowed_tools`, but as
  VISIBILITY only — the CLI has to expose a tool before it can be called. The
  hook is the gate. Both are needed and only one is load-bearing; the code says
  which.
- `mcp__server__*` is the only pattern. No general globbing: a pattern language
  in the one place where being subtly wrong means acting as somebody else is a
  second thing to get subtly wrong. `mcp__chat__chat_search*` matches nothing.
- Tool names arrive from the SDK, so malformed ones (`mcp__`, `mcp__chat`) are
  refused rather than parsed optimistically.
- Not covered: a tool that is read-only in name and not in effect. The list is
  an operator's judgement about a third-party server, and nothing here can check
  it. That is why the refusal prints the whole list — it is meant to be read
  back occasionally.
- The work deployment (`deploy/docker-compose.work.yml`) is the first user, with
  eleven read tools named and every chat write tool absent on purpose. See
  [[hookstack-verify-on-production]] for the habit this was verified under.
