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

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from hookprobe import gate
from hookprobe.engine_codex import CodexEngine, sandbox_for
from hookprobe.engine_codex import _Turn as _CodexTurn
from hookprobe.engine_pi import GATE_EXTENSION, PiEngine
from hookprobe.engine_pi import _Turn as _PiTurn
from hookprobe.runtimes import ADAPTERS, build_engine
from hookprobe.settings import Settings
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


def _drive(lines: list[str]) -> tuple[_CodexTurn, list[dict]]:
    seen: list[dict] = []
    turn = _CodexTurn(seen.append)
    for line in lines:
        turn.feed(line)
    return turn, seen


def _installed_cli(tmp_path: Path, name: str) -> str:
    """A path standing in for the CLI's presence — created, never run.

    `verify_gate` asks whether there is a CLI to drive at all before it proves
    the gate, and that question has its own test
    (`test_a_missing_runtime_is_caught_before_a_turn_is_accepted`). CI runners
    have neither codex nor pi installed, so the gate tests below stopped on that
    earlier check and asserted nothing about a gate — and skipping them there
    would have retired the two tests that cover the one failure this adapter has
    actually shipped. So the presence check is satisfied with a file that
    exists, and everything under it is untouched: the real gate, in a real
    subprocess, refusing a real command.
    """
    path = tmp_path / name
    path.write_text("", encoding="utf-8")
    return str(path)


# --------------------------------------------------------------- the registry


def test_every_adapter_is_covered_by_this_suite() -> None:
    """A runtime nothing here judges is a runtime nobody has checked.

    The registry is the list `build_engine` dispatches on, so this is the one
    assertion that cannot be satisfied by writing a new adapter and forgetting
    this file.
    """
    assert set(ADAPTERS) == {"claude", "codex", "pi"}


def test_the_contract_names_every_adapter_it_is_the_authority_for() -> None:
    """`hookprobe/docs/runtimes.md` calls the Engine docstring the authority, and
    for two days it was the authority on a world with one adapter in it: it
    still opened "One adapter exists ... the shape a second one has to fill"
    after codex and pi had both landed and both corrected it on a point its
    author could not have known.

    The test above pins the registry. This pins the RECORD to the registry, so a
    fourth adapter cannot arrive without the contract mentioning it — which is
    the only mechanism here that survives the person who wrote the contract
    forgetting about it.
    """
    from hookprobe.service import Engine

    doc = Engine.__doc__ or ""
    for name in ADAPTERS:
        assert f"`{name}`" in doc, f"the contract does not name the {name!r} adapter it governs"


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
    engine = CodexEngine(
        make_settings(
            tmp_path,
            runtime="codex",
            codex_binary=_installed_cli(tmp_path, "codex"),
            codex_python="/nonexistent/python",
        )
    )
    with pytest.raises(RuntimeError, match="cannot hold a posture"):
        engine.verify_gate()


def test_a_working_gate_is_proven_once(tmp_path: Path) -> None:
    engine = CodexEngine(
        make_settings(
            tmp_path,
            runtime="codex",
            codex_binary=_installed_cli(tmp_path, "codex"),
            codex_python=sys.executable,
        )
    )
    engine.verify_gate()
    assert engine._gate_proven, "a probe on every turn is a cost paid forever for an answer that cannot change"


def test_a_missing_runtime_is_caught_before_a_turn_is_accepted(tmp_path: Path) -> None:
    """Failing at boot beats failing on the first alert at three in the morning."""
    engine = CodexEngine(make_settings(tmp_path, runtime="codex", codex_binary="codex-that-is-not-installed"))
    with pytest.raises(RuntimeError, match="not on this node's PATH"):
        engine.verify_gate()


# Captured from a real pi turn against the same gateway, asked to run a command
# the gate refuses and then one it does not. Trimmed, never edited.
PI_STREAM = [
    '{"type":"session","version":3,"id":"01a0821c-0f2d-7029-a4c5-e5a63ae77503","cwd":"/data"}',
    '{"type":"tool_execution_start","toolCallId":"call_Q7ZBx","toolName":"bash",'
    '"args":{"command":"kubectl delete pod pi-canary; echo pi-ok","timeout":120}}',
    '{"type":"tool_execution_end","toolCallId":"call_Q7ZBx","toolName":"bash","isError":true,'
    '"result":{"content":[{"type":"text","text":"read-only guard: kubectl mutation or pod entry is blocked"}]}}',
    '{"type":"tool_execution_start","toolCallId":"call_Qqwoj","toolName":"bash",'
    '"args":{"command":"echo pi-ok","timeout":30}}',
    '{"type":"tool_execution_end","toolCallId":"call_Qqwoj","toolName":"bash","isError":false,'
    '"result":{"content":[{"type":"text","text":"pi-ok\\n"}]}}',
    '{"type":"message_end","message":{"role":"assistant","content":[{"type":"text",'
    '"text":"`kubectl delete pod pi-canary` was blocked by the read-only guard, while `echo pi-ok` ran."}],'
    '"usage":{"input":3,"output":30,"cacheRead":6289,"cacheWrite":75,"totalTokens":6397,'
    '"cost":{"input":0,"output":0,"cacheRead":0,"cacheWrite":0,"total":0}}}}',
]


def _drive_pi(lines: list[str]) -> tuple[_PiTurn, list[dict]]:
    seen: list[dict] = []
    turn = _PiTurn(seen.append)
    for line in lines:
        turn.feed(line)
    return turn, seen


def test_pi_puts_the_session_id_first_too() -> None:
    turn, seen = _drive_pi(PI_STREAM)
    assert seen[0] == {"type": "session", "id": "01a0821c-0f2d-7029-a4c5-e5a63ae77503"}
    assert turn.session_was_first


def test_pi_marks_a_refused_call_rather_than_hiding_it() -> None:
    """Where the two runtimes disagree, and the feed still has to read the same.

    Codex omits the tool entirely when the gate refuses. pi announces the call,
    preflights, and ends it with the refusal as its result — so the step exists
    and has to be marked, which is arguably the better record: it shows what the
    agent tried.
    """
    _, seen = _drive_pi(PI_STREAM)
    kinds = [event["type"] for event in seen]
    assert kinds == ["session", "tool_use", "tool_done", "tool_use", "tool_done", "text"]
    assert seen[2]["error"] is True, "a call the gate refused is not a call that succeeded"
    assert "error" not in seen[4]


def test_pi_reports_zero_cost_as_unknown_when_tokens_were_spent() -> None:
    """The trap in a runtime that DOES report cost.

    pi prices a turn from its model catalog. A model served through a private
    gateway is not in that catalog, so the number comes back 0 beside six
    thousand real tokens — the exact shape the ledger forbids. A budget breaker
    fed those would watch an unattended node spend all week and see nothing.
    """
    turn, _ = _drive_pi(PI_STREAM)
    result = turn.result(duration_ms=8000, returncode=0, stderr="")
    assert result.cost_usd is None
    assert result.usage is not None and result.usage["totalTokens"] == 6397


def test_pi_reports_a_real_price_when_it_has_one() -> None:
    priced = PI_STREAM[:-1] + [
        '{"type":"message_end","message":{"role":"assistant","content":[{"type":"text","text":"done"}],'
        '"usage":{"totalTokens":6397,"cost":{"total":0.0431}}}}'
    ]
    turn, _ = _drive_pi(priced)
    assert turn.result(duration_ms=1, returncode=0, stderr="").cost_usd == 0.0431


def test_pi_free_is_only_free_with_nothing_spent() -> None:
    free = PI_STREAM[:-1] + [
        '{"type":"message_end","message":{"role":"assistant","content":[{"type":"text","text":"done"}],'
        '"usage":{"totalTokens":0,"cost":{"total":0}}}}'
    ]
    turn, _ = _drive_pi(free)
    assert turn.result(duration_ms=1, returncode=0, stderr="").cost_usd == 0.0


def test_pi_reaches_the_same_gate(tmp_path: Path) -> None:
    engine = PiEngine(make_settings(tmp_path, runtime="pi", codex_python=sys.executable, pi_python=sys.executable))
    env = engine._env("probe:conformance:pi")
    assert env["HOOKPROBE_GATE_PYTHON"] == sys.executable
    assert env["HOOKPROBE_GATE_MODE"] == "readonly"
    done = subprocess.run(
        [env["HOOKPROBE_GATE_PYTHON"], "-m", "hookprobe.gate"],
        input=json.dumps(
            {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "kubectl delete ns prod"}}
        ),
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    assert json.loads(done.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_the_pi_gate_extension_ships_with_the_package() -> None:
    """Without it pi runs with no posture at all, so its absence stops the node."""
    assert GATE_EXTENSION.is_file()
    source = GATE_EXTENSION.read_text(encoding="utf-8")
    assert "hookprobe.gate" in source, "the extension must reach the one gate, not carry a second posture"
    assert "block: true" in source
    # It has to survive `pip install` too. It does not by default: package-data
    # listed only the console, so an image would have had the adapter and not
    # the posture it depends on.
    packaging = (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text(encoding="utf-8")
    assert "*.ts" in packaging


def test_pi_refuses_to_run_without_its_extension(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("hookprobe.engine_pi.GATE_EXTENSION", tmp_path / "gone.ts")
    engine = PiEngine(
        make_settings(
            tmp_path,
            runtime="pi",
            pi_binary=_installed_cli(tmp_path, "pi"),
            pi_python=sys.executable,
        )
    )
    with pytest.raises(RuntimeError, match="gate extension is missing"):
        engine.verify_gate()


def test_pi_does_not_trust_the_workspace(tmp_path: Path) -> None:
    """The workspace holds files a previous run wrote, so nothing in it is an input."""
    argv = PiEngine(make_settings(tmp_path, runtime="pi"))._argv("hello", None)
    assert "--no-approve" in argv
    assert argv[-2:] == ["--", "hello"], "a prompt that opens with a dash is a prompt, not a flag"


def test_pi_resume_names_the_session(tmp_path: Path) -> None:
    argv = PiEngine(make_settings(tmp_path, runtime="pi"))._argv("hello", "01a0821c-0f2d-7029-a4c5-e5a63ae77503")
    assert argv[argv.index("--session") + 1] == "01a0821c-0f2d-7029-a4c5-e5a63ae77503"


def test_pi_keeps_its_transcripts_on_the_persistent_volume(tmp_path: Path) -> None:
    engine = PiEngine(make_settings(tmp_path, runtime="pi"))
    assert Path(engine._env("k")["PI_CODING_AGENT_SESSION_DIR"]).is_relative_to(tmp_path)


# ------------------ the incumbent, held to the same five as the newcomers
#
# This section is the correction to an inversion. The suite was written to stop a
# NEW adapter satisfying the signatures and none of the obligations, and it did
# that: 30 assertions against codex, 17 against pi. It never once instantiated
# `ClaudeAgentEngine` — the default, and the only adapter any deployment
# actually runs. Obligation 1 had a claude test (`_bash_guard_hook`, above);
# obligations 2 through 5 were pinned only on the two engines nobody deploys, so
# breaking obligation 4 on the deployed path would have failed nothing.
#
# It needs the SDK's message classes, which is the same exception
# `tests/test_engine_loop.py` documents and for the same reason: a declared
# dependency in this venv, no subprocess, no model, no credential. The fake
# client is local rather than shared with that file — the authority should not
# depend on a behaviour suite that can change for unrelated reasons.


class _FakeClaudeClient:
    scripted: list[Any] = []

    def __init__(self, options: Any = None) -> None:
        type(self).options = options

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def query(self, message: str) -> None: ...

    # Present because the engine reaches for it on the stop path; a client
    # without it turns an interrupt into an AttributeError mid-turn.
    async def interrupt(self) -> None: ...

    async def receive_response(self):
        for message in type(self).scripted:
            yield message


def _drive_claude(
    tmp_path: Path, messages: list[Any], monkeypatch: pytest.MonkeyPatch, **kw: Any
) -> tuple[Any, list[dict]]:
    import claude_agent_sdk

    from hookprobe.engine import ClaudeAgentEngine

    monkeypatch.setattr(claude_agent_sdk, "ClaudeSDKClient", _FakeClaudeClient)
    _FakeClaudeClient.scripted = messages
    engine = ClaudeAgentEngine(make_settings(tmp_path, workdir=tmp_path))
    seen: list[dict] = []
    result = asyncio.run(
        engine.run(message="investigate", session_key="probe:conformance:1", on_event=seen.append, **kw)
    )
    return result, seen


def _claude_result(**over: Any) -> Any:
    from claude_agent_sdk import ResultMessage

    base: dict[str, Any] = {
        "subtype": "success",
        "duration_ms": 1200,
        "duration_api_ms": 900,
        "is_error": False,
        "num_turns": 1,
        "session_id": "01a081ff-claude",
        "total_cost_usd": 0.25,
        "result": "the report",
        "usage": {"input_tokens": 10, "output_tokens": 20},
    }
    return ResultMessage(**{**base, **over})


def test_the_claude_adapter_records_every_tool_call_where_the_agent_cannot(tmp_path: Path) -> None:
    """Obligation 2, on the incumbent. Codex has had this since it landed.

    The hook is what writes it, which is why this drives the hook rather than a
    turn: the same mechanism fires inside subagents, whose calls never appear in
    the message stream at all.
    """
    from hookprobe.engine import _audit_hook

    audit = tmp_path / "audit"
    hook = _audit_hook(audit, "probe:conformance:1")
    asyncio.run(hook({"tool_name": "Bash", "tool_input": {"command": "kubectl get pods"}}, None, None))
    asyncio.run(
        hook(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "kubectl delete pod x"},
                "tool_response": {"is_error": True},
            },
            None,
            None,
        )
    )

    # Read the way the codex audit tests read it: one day-file of JSONL.
    written = sorted(audit.glob("*.jsonl"))
    assert len(written) == 1, "the flight recorder writes one file per day, or nothing is being recorded"
    lines = [json.loads(raw) for raw in written[0].read_text(encoding="utf-8").splitlines() if raw.strip()]
    assert [entry["tool"] for entry in lines] == ["Bash", "Bash"]
    assert [entry["error"] for entry in lines] == [False, True], "a refusal is recorded too, or the account is partial"
    assert all(entry["session"] == "probe:conformance:1" for entry in lines)


def test_the_claude_adapter_puts_the_session_id_first_too(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Obligation 4, on the incumbent, and the one whose absence cost real money.

    An id read off the RESULT leaves a turn killed mid-flight with nothing to
    continue; the service then failed the run and an operator paid for the whole
    investigation twice. All three adapters emit it the moment the runtime first
    says it — engine.py:962, engine_codex.py:345, engine_pi.py:259 — and until
    now only the two that nobody deploys had a test saying so.
    """
    from claude_agent_sdk import AssistantMessage, StreamEvent, TextBlock

    # A real turn's first message is a token delta, and on this SDK the delta is
    # what carries the id: `AssistantMessage` has no `session_id` field at all,
    # so the emission depends on `include_partial_messages=True` (engine.py:884)
    # being on. Pinned below, because turning it off to save bandwidth would
    # silently move this adapter back to result-only ids and take mid-turn
    # recovery with it — the exact regression that cost a double-paid
    # investigation the first time.
    delta = StreamEvent(
        uuid="u",
        session_id="01a081ff-claude",
        event={"type": "content_block_delta", "delta": {"type": "text_delta", "text": "look"}},
    )
    _, seen = _drive_claude(
        tmp_path,
        [delta, AssistantMessage(content=[TextBlock(text="looking")], model="m"), _claude_result()],
        monkeypatch,
    )
    kinds = [event["type"] for event in seen]
    assert kinds[0] == "session", f"the id has to arrive before anything else, got {kinds}"
    assert seen[0]["id"] == "01a081ff-claude"
    assert getattr(_FakeClaudeClient.options, "include_partial_messages", None) is True, (
        "this adapter's mid-turn id rides the partial-message stream; without it the id is result-only"
    )

    # And with no deltas at all the id still arrives, late, with the result — a
    # turn that got that far is recoverable afterwards even though it was never
    # recoverable DURING.
    _, late = _drive_claude(
        tmp_path, [AssistantMessage(content=[TextBlock(text="looking")], model="m"), _claude_result()], monkeypatch
    )
    assert [event["type"] for event in late].index("session") >= 0


def test_the_claude_adapter_carries_a_session_across_a_boot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Obligation 3, on the incumbent: the id comes back on the result, and a
    resume is handed to the runtime rather than quietly dropped."""
    result, _ = _drive_claude(tmp_path, [_claude_result()], monkeypatch, resume="01a081ff-claude")
    assert result.session_id == "01a081ff-claude"
    assert getattr(_FakeClaudeClient.options, "resume", None) == "01a081ff-claude", (
        "an adapter that accepts `resume` and does not pass it on satisfies the type and loses the feature"
    )


def test_the_claude_adapter_does_not_report_a_zero_it_cannot_stand_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Obligation 5, on the incumbent — pi's correction, applied before it bites.

    This adapter passed `total_cost_usd` through raw and had never been seen
    reporting a zero: 89 runs on the work deployment, every one priced. It also
    runs a gateway model the CLI does not price, which is precisely the condition
    that produced pi's `totalTokens: 6397` with `cost.total: 0`. The rule is one
    function (`engine.priced`) for both, because a rule about money written twice
    is how `cost_usd` comes to mean two things.
    """
    unpriced, _ = _drive_claude(tmp_path, [_claude_result(total_cost_usd=0.0)], monkeypatch)
    assert unpriced.cost_usd is None, "0.0 with 30 tokens behind it is 'nobody priced this', not 'this was free'"

    priced_turn, _ = _drive_claude(tmp_path, [_claude_result(total_cost_usd=0.25)], monkeypatch)
    assert priced_turn.cost_usd == 0.25, "a real price still passes through"

    free, _ = _drive_claude(
        tmp_path, [_claude_result(total_cost_usd=0.0, usage={"input_tokens": 0, "output_tokens": 0})], monkeypatch
    )
    assert free.cost_usd == 0.0, "and a zero with nothing behind it is allowed to mean free"


def test_the_node_reports_a_runtime_this_build_can_actually_run(tmp_path: Path) -> None:
    """What `/v1/agent` says it runs must be a name the registry dispatches on.

    Written because it was not: the field arrived hard-coded to the literal
    `"claude-code"` when there was one adapter and no registry to name
    (d6b6826), and became `settings.runtime` the day the second one landed
    (fc18863). Nodes deployed in between still answer `claude-code` — a name
    `build_engine` now refuses outright. A comment beside the field says a node
    reporting one runtime while running another is the claim this contract
    exists to keep honest; this is that claim as an assertion.
    """
    settings = make_settings(tmp_path, runtime="claude")
    assert settings.runtime in ADAPTERS
    # And the default, for a deployment that sets no HOOKPROBE_RUNTIME at all —
    # which is every deployment in this repository today.
    default = Settings.__dataclass_fields__["runtime"].default
    assert default in ADAPTERS, f"the field's default {default!r} is not a runtime this build has an adapter for"


# ------------------ the check that verify() alone could not make: was it ASKED?


def test_a_turn_that_ran_tools_without_a_gate_record_stops_the_node(tmp_path: Path) -> None:
    """`verify()` proves the gate ANSWERS; this proves the runtime ASKED it.

    They look identical from inside the process and they are not. Driving codex
    through its app-server leaves a perfectly working gate command unused: the
    self-test passes, /v1/agent reports the posture, and `kubectl delete` runs.
    Measured, and the reason this check exists.
    """
    engine = CodexEngine(make_settings(tmp_path, runtime="codex", codex_python=sys.executable))
    state = _CodexTurn(lambda event: None)
    state.tool_calls = 3  # a turn that used tools
    engine._check_gate_was_consulted(state, "probe:ungated:1", since=time.time())
    assert engine._gate_broken is not None
    with pytest.raises(RuntimeError, match="posture was not enforced"):
        engine.verify_gate()


def test_a_turn_with_no_tools_is_not_accused(tmp_path: Path) -> None:
    engine = CodexEngine(make_settings(tmp_path, runtime="codex"))
    state = _CodexTurn(lambda event: None)
    engine._check_gate_was_consulted(state, "probe:quiet:1", since=time.time())
    assert engine._gate_broken is None


def test_a_recorded_call_clears_the_check(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, runtime="codex")
    since = time.time()
    gate.decide(
        {"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {"command": "ls"}},
        CodexEngine(settings)._env("probe:gated:1"),
    )
    assert gate.consulted(tmp_path / "audit", "probe:gated:1", since=since)


def test_the_pi_adapter_makes_the_same_check(tmp_path: Path) -> None:
    engine = PiEngine(make_settings(tmp_path, runtime="pi"))
    state = _PiTurn(lambda event: None)
    state.tool_calls = 1
    engine._check_gate_was_consulted(state, "probe:ungated:pi", since=time.time())
    assert engine._gate_broken is not None


# --------------- the guards nothing had watched fire on these runtimes


def test_the_input_guard_reads_a_codex_patch_envelope(tmp_path: Path) -> None:
    """Codex does not edit through a tool with a path argument.

    It sends one `apply_patch` call whose whole patch is a string, so a guard
    reading argument keys sees an unknown tool carrying no path and lets it
    through. Measured against a real turn: the call arrived as
    `tool_name='apply_patch'`, `tool_input={'command': '*** Begin Patch…'}`.
    """
    (tmp_path / "CLAUDE.md").write_text("steering", encoding="utf-8")
    patch = f"*** Begin Patch\n*** Update File: {tmp_path / 'CLAUDE.md'}\n@@\n-steering\n+mine\n*** End Patch"
    verdict = gate.deny_reason("apply_patch", {"command": patch}, workdir=tmp_path)
    assert verdict is not None and verdict[0] == "input"


def test_a_patch_that_touches_nothing_protected_is_allowed(tmp_path: Path) -> None:
    patch = f"*** Begin Patch\n*** Add File: {tmp_path / 'notes.md'}\n+hello\n*** End Patch"
    assert gate.deny_reason("apply_patch", {"command": patch}, workdir=tmp_path) is None


def test_agents_md_steers_the_next_run_too(tmp_path: Path) -> None:
    """The guard protected 'the files that steer the next run' on one runtime.

    Codex and pi both read AGENTS.md as standing instructions, so leaving it out
    meant a run on either could rewrite what the next one is told.
    """
    (tmp_path / "AGENTS.md").write_text("standing instructions", encoding="utf-8")
    verdict = gate.deny_reason("Edit", {"path": str(tmp_path / "AGENTS.md")}, workdir=tmp_path)
    assert verdict is not None and verdict[0] == "input"


@pytest.mark.parametrize(
    "command",
    [
        "printf 'x' >> CLAUDE.md",
        "echo x > AGENTS.md",
        "tee CLAUDE.md",
        "sed -i '' s/a/b/ CLAUDE.md",
        "sed --in-place s/a/b/ AGENTS.md",
        "cp /tmp/evil CLAUDE.md",
        "mv /tmp/evil .claude/skills/x",
    ],
)
def test_a_shell_redirect_is_not_a_way_around_the_input_guard(tmp_path: Path, command: str) -> None:
    """It was, on every runtime and under both postures, until it was measured.

    The input guard reads the arguments of Write and Edit. A Bash call has no
    path argument, so nothing looked at where the bytes were going.
    """
    for name in ("CLAUDE.md", "AGENTS.md"):
        (tmp_path / name).write_text("x", encoding="utf-8")
    verdict = gate.deny_reason("Bash", {"command": command}, bash_mode="danger-only", workdir=tmp_path)
    assert verdict is not None and verdict[0] == "input", f"{command!r} reached a steering file"


@pytest.mark.parametrize(
    "command",
    ["echo hi > notes.md", "cat CLAUDE.md", "grep x CLAUDE.md | head -3", "ls -la", "kubectl get pods"],
)
def test_reading_a_steering_file_is_still_allowed(tmp_path: Path, command: str) -> None:
    """`Paths this runner may read but never write` — the reading half matters."""
    (tmp_path / "CLAUDE.md").write_text("x", encoding="utf-8")
    assert gate.deny_reason("Bash", {"command": command}, bash_mode="danger-only", workdir=tmp_path) is None


def test_the_claude_adapter_asks_the_same_question(tmp_path: Path) -> None:
    """One decision, three runtimes. The in-process hook takes the workdir now."""
    import asyncio

    from hookprobe.engine import _bash_guard_hook

    (tmp_path / "CLAUDE.md").write_text("x", encoding="utf-8")
    hook = _bash_guard_hook("danger-only", None, tmp_path, None)
    answer = asyncio.run(hook({"tool_input": {"command": "echo x > CLAUDE.md"}}, None, None))
    assert answer["hookSpecificOutput"]["permissionDecision"] == "deny"
