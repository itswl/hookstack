"""Loop hygiene: the repeat reminder, command deadlines, and the inputs record."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from hookprobe.hygiene import RepeatWatch, post_tool_hook


def test_repeat_watch_reminds_on_the_threshold_and_its_multiples() -> None:
    watch = RepeatWatch(3)
    args = {"command": "kubectl get pods"}

    assert watch.note("Bash", args) is None
    assert watch.note("Bash", args) is None
    third = watch.note("Bash", args)
    assert third is not None
    assert "#3" in third and "Bash" in third
    assert watch.note("Bash", args) is None  # 4th stays quiet
    assert watch.note("Bash", args) is None
    assert watch.note("Bash", args) is not None  # 6th reminds again


def test_repeat_watch_separates_different_calls_and_can_be_disabled() -> None:
    watch = RepeatWatch(2)
    assert watch.note("Bash", {"command": "a"}) is None
    assert watch.note("Bash", {"command": "b"}) is None
    assert watch.note("Bash", {"command": "a"}) is not None

    off = RepeatWatch(0)
    for _ in range(10):
        assert off.note("Bash", {"command": "a"}) is None


def test_unserialisable_arguments_do_not_break_counting() -> None:
    watch = RepeatWatch(2)
    weird = {"handle": object()}
    assert watch.note("Bash", weird) is None
    assert watch.note("Bash", weird) is not None


def test_hook_is_silent_when_there_is_nothing_to_say() -> None:
    hook = post_tool_hook(session_key="probe:inbound:4", repeat_reminder_at=3)
    result = asyncio.run(
        hook({"tool_name": "Read", "tool_input": {"file_path": "/data/x"}, "tool_response": "tiny"}, "c", None)
    )
    assert result == {}


def test_hook_never_raises_on_a_malformed_payload() -> None:
    hook = post_tool_hook(session_key="probe:inbound:5", repeat_reminder_at=3)
    assert asyncio.run(hook({}, None, None)) == {}


def test_hook_delivers_the_reminder_as_additional_context() -> None:
    hook = post_tool_hook(session_key="probe:inbound:6", repeat_reminder_at=2)
    payload = {"tool_name": "Bash", "tool_input": {"command": "kubectl get pods"}, "tool_response": {"stdout": "x"}}

    assert asyncio.run(hook(dict(payload), "call-1", None)) == {}
    second = asyncio.run(hook(dict(payload), "call-2", None))

    assert second["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert "identical arguments" in second["hookSpecificOutput"]["additionalContext"]
    # Output is never rewritten: the harness owns oversized tool results.
    assert "updatedToolOutput" not in second["hookSpecificOutput"]


def test_engine_records_the_prompt_inputs_it_resolved(tmp_path: Path) -> None:
    """The record must name the memory and skills that were actually loaded."""
    from hookprobe.engine import ClaudeAgentEngine
    from tests.helpers import make_settings

    (tmp_path / "CLAUDE.md").write_text("Report in English.", encoding="utf-8")
    (tmp_path / "system-prompt.md").write_text("Prefer logs before metrics.", encoding="utf-8")
    skill = tmp_path / ".claude" / "skills" / "gateway-5xx"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# gateway 5xx", encoding="utf-8")
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "log-analyst.md").write_text("---\nname: log-analyst\n---\n", encoding="utf-8")
    mcp = tmp_path / "mcp.json"
    mcp.write_text(json.dumps({"prometheus": {"command": "npx", "args": []}}), encoding="utf-8")

    engine = ClaudeAgentEngine(make_settings(tmp_path, mcp_config=mcp))
    inputs = engine.describe_inputs(resume="sdk-session-1")

    assert inputs["model"] == "claude-opus-5"
    assert inputs["resumed"] is True
    assert inputs["skills"]["project"] == ["gateway-5xx"]
    assert "user" not in inputs["skills"]  # setting_sources is project-only here
    assert inputs["agents"]["files"] == ["log-analyst"]
    assert inputs["mcp_servers"] == ["prometheus"]
    assert inputs["memory"]["bytes"] == len("Report in English.")
    assert len(inputs["memory"]["sha256"]) == 12
    assert inputs["system_prompt_append"]["bytes"] == len("Prefer logs before metrics.")
    assert inputs["hygiene"]["repeat_reminder_at"] == 3


def test_inputs_record_reports_absent_files_as_absent(tmp_path: Path) -> None:
    from hookprobe.engine import ClaudeAgentEngine
    from tests.helpers import make_settings

    inputs = ClaudeAgentEngine(make_settings(tmp_path)).describe_inputs()

    assert inputs["memory"] is None
    assert inputs["system_prompt_append"] is None
    assert inputs["mcp_servers"] == []
    assert inputs["resumed"] is False


def test_bash_deadlines_are_handed_to_the_engine(tmp_path: Path) -> None:
    from hookprobe.engine import ClaudeAgentEngine
    from tests.helpers import make_settings

    armed = ClaudeAgentEngine(make_settings(tmp_path, bash_timeout_ms=30000, bash_max_timeout_ms=90000))
    assert armed._engine_env() == {"BASH_DEFAULT_TIMEOUT_MS": "30000", "BASH_MAX_TIMEOUT_MS": "90000"}

    # Zero means "leave the CLI's own default alone", not "no timeout".
    unset = ClaudeAgentEngine(make_settings(tmp_path, bash_timeout_ms=0, bash_max_timeout_ms=0))
    assert unset._engine_env() == {}
