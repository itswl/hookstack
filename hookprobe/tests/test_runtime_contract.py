"""The Runtime Contract, as a suite any adapter has to pass.

`service.Engine` had one implementation for its whole life, and the note that
parked this work said the plain thing about that: a contract satisfied once is
a description of that implementation. Nothing in the old suite would have
noticed an adapter that met every signature and none of the obligations.

So this file asserts the obligations, not an implementation. It is deliberately
shaped like `deploy/lark-bridge/tests/test_protocol.py`, where one fixture
drives both ends of the chat protocol: the third adapter's author copies
nothing, they add a row to `ADAPTERS` and find out what is missing.

Two of the five obligations are invisible in the type, and those are the two
tested hardest here — with the real gate, in a real subprocess, refusing a real
command. The rest of the suite would pass against a well-mannered fake; the
gate tests would not.

The stream fixtures below are real bytes, captured from codex-cli 0.153.4
running against the deployment's own model. Recorded rather than live because
the parsing is the part of an adapter most likely to be wrong, and finding that
out should not cost a paid model run.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from hookprobe import gate
from hookprobe.engine_codex import CodexEngine, _Turn, sandbox_for
from hookprobe.runtimes import ADAPTERS, build_engine
from tests.helpers import make_settings

# Captured from a real turn: `codex exec --json` asked to run one shell command.
# The first line is the whole of obligation four.
CODEX_STREAM = [
    '{"type":"thread.started","thread_id":"01a081ff-506b-7e72-9e1f-033618890dc4"}',
    '{"type":"turn.started"}',
    '{"type":"item.started","item":{"id":"item_2","type":"command_execution",'
    '"command":"/bin/zsh -lc \'echo hello\'","aggregated_output":"","exit_code":null,"status":"in_progress"}}',
    '{"type":"item.completed","item":{"id":"item_2","type":"command_execution",'
    '"command":"/bin/zsh -lc \'echo hello\'","aggregated_output":"hello\\n","exit_code":0,"status":"completed"}}',
    '{"type":"item.completed","item":{"id":"item_3","type":"agent_message","text":"hello"}}',
    '{"type":"turn.completed","usage":{"input_tokens":23511,"cached_input_tokens":11706,'
    '"cache_write_input_tokens":11799,"output_tokens":73,"reasoning_output_tokens":8}}',
]

# The same run with the gate saying no: captured after a PreToolUse hook returned
# `permissionDecision: deny`. Note what is NOT here — no command_execution item
# at all. The tool did not run; it was not run and then reported.
CODEX_DENIED_STREAM = [
    '{"type":"thread.started","thread_id":"01a08205-718a-77e2-984c-bf33f14ac200"}',
    '{"type":"turn.started"}',
    '{"type":"item.completed","item":{"id":"item_2","type":"agent_message",'
    '"text":"The command was blocked by the read-only environment and did not run."}}',
    '{"type":"turn.completed","usage":{"input_tokens":23609,"output_tokens":145}}',
]


def _drive(lines: list[str]) -> tuple[_Turn, list[dict]]:
    seen: list[dict] = []
    turn = _Turn(seen.append)
    for line in lines:
        turn.feed(line)
    return turn, seen


# --------------------------------------------------------------- the registry


def test_every_adapter_is_covered_by_this_suite() -> None:
    """A runtime nothing here judges is a runtime nobody has checked.

    The registry is the list `build_engine` dispatches on, so this is the one
    assertion that cannot be satisfied by writing a new adapter and forgetting
    this file.
    """
    assert set(ADAPTERS) == {"claude", "codex"}


def test_an_unknown_runtime_is_refused_rather_than_defaulted(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, runtime="codx")
    with pytest.raises(ValueError, match="not a runtime"):
        build_engine(settings)


def test_the_declared_runtime_is_the_one_built(tmp_path: Path) -> None:
    engine = build_engine(make_settings(tmp_path, runtime="codex"))
    assert isinstance(engine, CodexEngine)


# ------------------------------------------- obligation 1: a gate before a tool


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("Bash", {"command": "kubectl delete pod api-7f9"}),
        ("mcp__chat__send_message", {}),
    ],
)
def test_the_gate_refuses_through_the_spawned_command(tmp_path: Path, tool: str, tool_input: dict) -> None:
    """The gate as a runtime actually reaches it: a subprocess, over stdin.

    Not `gate.decide(...)` called in-process. Codex spawns a command and reads
    one line of stdout, so that is what is exercised — a posture that only holds
    when imported is a posture that does not hold.
    """
    settings = make_settings(tmp_path, runtime="codex", bash_guard="readonly", codex_python=sys.executable)
    engine = CodexEngine(settings)
    payload = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input}

    # The adapter's own command and the adapter's own environment, with nothing
    # added. An earlier version of this test put `sys.path` into PYTHONPATH and
    # so proved only that the gate works when someone else makes it importable —
    # which is precisely how the first live run shipped without a gate at all.
    done = subprocess.run(
        [settings.codex_python, "-m", "hookprobe.gate"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=engine._env("probe:conformance:1"),
        check=True,
    )
    answer = json.loads(done.stdout)
    assert answer["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert answer["hookSpecificOutput"]["permissionDecisionReason"]


def test_the_gate_lets_an_observation_through(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, runtime="codex")
    answer = gate.decide(
        {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "kubectl get pods"}},
        CodexEngine(settings)._env("probe:conformance:1"),
    )
    assert answer == {}, "a read-only node still has to be able to read"


def test_the_gate_fails_closed(tmp_path: Path) -> None:
    """A gate whose failure mode is permissive is the one you never find out about."""
    done = subprocess.run(
        [sys.executable, "-m", "hookprobe.gate"],
        input='{"hook_event_name":"PreToolUse", this is not json',
        capture_output=True,
        text=True,
        env=CodexEngine(make_settings(tmp_path, codex_python=sys.executable))._env("probe:conformance:closed"),
        check=True,
    )
    assert json.loads(done.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_the_adapter_installs_the_gate_where_the_runtime_will_look(tmp_path: Path) -> None:
    """The hooks file is the adapter's half of obligation one."""
    engine = CodexEngine(make_settings(tmp_path, runtime="codex"))
    home = engine.prepare_home()
    hooks = json.loads((home / "hooks.json").read_text())["hooks"]
    assert "PreToolUse" in hooks, "no pre-tool gate means this runtime cannot be run under a posture"
    assert "hookprobe.gate" in hooks["PreToolUse"][0]["hooks"][0]["command"]


def test_the_sandbox_matches_the_declared_posture(tmp_path: Path) -> None:
    assert sandbox_for("readonly") == "read-only"
    assert sandbox_for("danger-only") == "workspace-write"


# ------------------------------------- obligation 2: an audit the agent can't edit


def test_every_tool_call_reaches_the_flight_recorder(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, runtime="codex")
    env = CodexEngine(settings)._env("probe:conformance:2")
    gate.decide(
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "kubectl get pods -A"},
            "tool_response": "…",
        },
        env,
    )
    written = sorted((tmp_path / "audit").glob("*.jsonl"))
    assert written, "a tool call that leaves no record is a run's own word"
    line = json.loads(written[0].read_text().strip().splitlines()[-1])
    assert line["session"] == "probe:conformance:2"
    assert line["tool"] == "Bash"


def test_a_refusal_is_recorded_too(tmp_path: Path) -> None:
    """The refusals are the strongest evidence the agent did not do as told."""
    settings = make_settings(tmp_path, runtime="codex")
    env = CodexEngine(settings)._env("probe:conformance:3")
    gate.decide(
        {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "kubectl delete ns prod"}},
        env,
    )
    line = json.loads(sorted((tmp_path / "audit").glob("*.jsonl"))[0].read_text().strip().splitlines()[-1])
    assert line["denied"] is True and line["guard"] == "bash"


def test_the_gate_inherits_its_posture_from_the_environment(tmp_path: Path) -> None:
    """Nothing in the payload can widen it, because nothing there is read."""
    settings = make_settings(tmp_path, runtime="codex", bash_guard="readonly")
    env = CodexEngine(settings)._env("probe:conformance:4")
    answer = gate.decide(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "kubectl delete pod x"},
            "permission_mode": "bypassPermissions",
            "bash_guard": "off",
        },
        env,
    )
    assert answer["hookSpecificOutput"]["permissionDecision"] == "deny"


# ------------------------------- obligation 3: a session id that outlives a boot


def test_a_resume_names_the_session_on_the_command_line(tmp_path: Path) -> None:
    """recover_orphans continues a run from a PREVIOUS boot, so the id has to
    reach a runtime that reads it off disk rather than out of this process."""
    engine = CodexEngine(make_settings(tmp_path, runtime="codex"))
    argv = engine._argv("01a081ff-506b-7e72-9e1f-033618890dc4")
    # The id has to follow `resume` immediately, and `resume` has to come after
    # the flags: `exec resume <id> --sandbox …` is refused by the CLI outright,
    # and the first attempt at this returned an empty run rather than an error.
    assert argv[argv.index("resume") + 1] == "01a081ff-506b-7e72-9e1f-033618890dc4"
    assert argv.index("resume") > argv.index("--sandbox")
    assert argv[-1] == "-", "the prompt goes on stdin, after every positional the CLI expects"
    assert "resume" not in engine._argv(None)


def test_the_session_store_lives_on_the_persistent_volume(tmp_path: Path) -> None:
    engine = CodexEngine(make_settings(tmp_path, runtime="codex"))
    assert engine.prepare_home().is_relative_to(tmp_path), (
        "a session store under the container's own filesystem does not survive the restart that resume exists for"
    )


# -------------------------------- obligation 4: the id, the moment it is known


def test_the_session_id_is_the_first_thing_emitted() -> None:
    turn, seen = _drive(CODEX_STREAM)
    assert seen[0] == {"type": "session", "id": "01a081ff-506b-7e72-9e1f-033618890dc4"}
    assert turn.session_id == seen[0]["id"]
    assert turn.session_was_first, "emitting it with the result is what made an interrupted first turn unrecoverable"


def test_the_stream_becomes_the_process_feed() -> None:
    _, seen = _drive(CODEX_STREAM)
    kinds = [event["type"] for event in seen]
    assert kinds == ["session", "tool_use", "tool_done", "text"]
    tool = seen[1]
    assert tool["name"] == "Bash" and "echo hello" in tool["detail"]
    assert seen[2]["id"] == tool["id"], "a duration with no step to attach to is a subagent, not a Bash call"


def test_a_refused_tool_never_becomes_a_tool_call() -> None:
    """What the denied run actually looked like: no tool step at all."""
    _, seen = _drive(CODEX_DENIED_STREAM)
    assert [event["type"] for event in seen] == ["session", "text"]


# ------------------------------------ obligation 5: cost, or None, but never 0.0


def test_tokens_are_kept_and_cost_stays_unknown() -> None:
    turn, _ = _drive(CODEX_STREAM)
    result = turn.result(duration_ms=1200, returncode=0, stderr="")
    assert result.cost_usd is None, "0.0 would tell the budget breaker this turn was free"
    assert result.usage is not None and result.usage["input_tokens"] == 23511
    assert result.session_id == "01a081ff-506b-7e72-9e1f-033618890dc4"
    assert result.text == "hello"


def test_a_failed_turn_still_reports_what_it_knows() -> None:
    turn, _ = _drive(CODEX_STREAM[:2])
    result = turn.result(duration_ms=90, returncode=1, stderr="stream error: 503 upstream unavailable")
    assert result.error and "503" in result.error
    assert result.cost_usd is None
    assert result.session_id, "a turn that failed after starting is still resumable"


def test_a_transient_failure_is_recognisable_as_one() -> None:
    """The retry decision reads this string, so the adapter must not invent a
    private vocabulary of failures."""
    from hookprobe.engine import transient

    turn, _ = _drive(CODEX_STREAM[:1])
    result = turn.result(duration_ms=90, returncode=1, stderr="API Error: 529 overloaded")
    assert result.error is not None
    assert transient(result.error)


def test_a_node_that_cannot_gate_refuses_to_run(tmp_path: Path) -> None:
    """The check that would have caught the first live run of this adapter.

    It ran with no gate: the hook command could not import hookprobe, codex
    logged it and carried on, and a `kubectl delete` executed on a node
    reporting `bash_guard: readonly`. Everything behaved reasonably and the
    posture was simply absent.
    """
    engine = CodexEngine(make_settings(tmp_path, runtime="codex", codex_python="/nonexistent/python"))
    with pytest.raises(RuntimeError, match="cannot hold a posture"):
        engine.verify_gate()


def test_a_working_gate_is_proven_once(tmp_path: Path) -> None:
    engine = CodexEngine(make_settings(tmp_path, runtime="codex", codex_python=sys.executable))
    engine.verify_gate()
    assert engine._gate_proven, "a probe on every turn is a cost paid forever for an answer that cannot change"


def test_a_missing_runtime_is_caught_before_a_turn_is_accepted(tmp_path: Path) -> None:
    """Failing at boot beats failing on the first alert at three in the morning."""
    engine = CodexEngine(make_settings(tmp_path, runtime="codex", codex_binary="codex-that-is-not-installed"))
    with pytest.raises(RuntimeError, match="not on this node's PATH"):
        engine.verify_gate()
