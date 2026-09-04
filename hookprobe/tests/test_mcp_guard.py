"""Mounting a server is plumbing; naming its tools is policy.

The investigator is the component that reads attacker-influenced text, and an
MCP server is not read-only just because you wanted it to be: a chat server ships
`chat_send_message` beside `chat_search_messages`. Before this guard,
"let the planner read the thread" and "let a message in that thread post as the
operator" were the same mount.

Enforced as a PreToolUse hook rather than through allowed_tools because the
engine runs with permission_mode="bypassPermissions" — same reasoning, and same
mechanism, as the bash guard.
"""

from __future__ import annotations

from hookprobe.engine import mcp_deny_reason

READ_ONLY = frozenset(
    {
        "mcp__chat__chat_search_messages",
        "mcp__chat__chat_list_conversations",
    }
)


def test_a_named_tool_runs() -> None:
    assert mcp_deny_reason("mcp__chat__chat_search_messages", READ_ONLY) is None


def test_the_write_tool_next_to_it_does_not() -> None:
    """The whole point. Same server, same mount, one letter apart in a config."""
    reason = mcp_deny_reason("mcp__chat__chat_send_message", READ_ONLY)
    assert reason is not None
    assert "not in HOOKPROBE_MCP_TOOLS" in reason
    assert "chat_search_messages" in reason, "the refusal says what this instance CAN do"


def test_empty_denies_everything_and_says_what_to_set() -> None:
    """Closed when unconfigured, like every other security door in this family:
    mounting a server must not be what grants its tools."""
    reason = mcp_deny_reason("mcp__chat__chat_search_messages", frozenset())
    assert reason is not None
    assert "HOOKPROBE_MCP_TOOLS" in reason


def test_a_whole_server_can_be_named() -> None:
    allowed = frozenset({"mcp__webhookwise__*"})
    assert mcp_deny_reason("mcp__webhookwise__list_recent_alerts", allowed) is None
    assert mcp_deny_reason("mcp__webhookwise__get_ai_cost_stats", allowed) is None
    assert mcp_deny_reason("mcp__chat__chat_send_message", allowed) is not None, (
        "a wildcard opens ONE server, not the habit of wildcards"
    )


def test_a_wildcard_is_not_a_glob() -> None:
    """`mcp__server__*` is the only pattern. A prefix that happens to match must
    not open anything — a pattern language here is a second thing to get subtly
    wrong, in the one place being subtly wrong means acting as somebody else."""
    allowed = frozenset({"mcp__chat__chat_search*", "mcp__ch*"})
    assert mcp_deny_reason("mcp__chat__chat_search_messages", allowed) is not None


def test_non_mcp_tools_are_not_this_guards_business() -> None:
    """Bash, Read and the write tools have their own guards; this one returning
    a reason for them would double-answer a question already decided."""
    for tool in ("Bash", "Read", "Write", "WebFetch", "Task"):
        assert mcp_deny_reason(tool, frozenset()) is None


def test_a_malformed_tool_name_is_refused_not_crashed() -> None:
    """The name arrives from the SDK, so it is not ours to trust the shape of."""
    for name in ("mcp__", "mcp__chat", "mcp____"):
        assert mcp_deny_reason(name, READ_ONLY) is not None
