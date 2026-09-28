"""The rehearsal, held to the Runtime Contract like the runtimes it stands beside.

`replay` is the one adapter that could most easily be waved through as "just a
demo", so it is in the registry and judged. What is asserted here is what a
thing that runs no model can still get wrong: the order of its events, whether
a refused call is refused by the REAL gate and recorded in the REAL audit,
whether its zero is an honest zero, and whether the procedure its report
proposes is one the allowlist it ships with would actually let run.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from hookprobe import audit, remediation
from hookprobe.engine_replay import (
    REHEARSAL_ALLOWLIST,
    SCRIPTS_DIR,
    ReplayEngine,
    choose,
    load_scripts,
    session_id_for,
)
from hookprobe.runs import COMPLETED, RunStore
from hookprobe.runtimes import build_engine
from hookprobe.service import RunService
from tests.helpers import make_settings

DISK = (
    "Run one read-only investigation.\n\nSource: inbound\nLevel: high\n"
    "Title: Disk /data at 92% on db-1\nBody: 3.4G left"
)
GATEWAY = (
    "Run one read-only investigation.\n\nSource: inbound\nLevel: high\nTitle: Payment gateway 5xx rate 8.1%\nBody: x"
)


def _engine(tmp_path: Path, **overrides: Any) -> ReplayEngine:
    return ReplayEngine(make_settings(tmp_path, runtime="replay", **overrides), pace=0.0)


def _drive(engine: ReplayEngine, message: str, key: str, resume: str | None = None) -> tuple[Any, list[dict]]:
    seen: list[dict] = []
    result = asyncio.run(engine.run(message=message, session_key=key, resume=resume, on_event=seen.append))
    return result, seen


# ------------------------------------------------------------- the registry


def test_the_registry_builds_the_rehearsal(tmp_path: Path) -> None:
    assert isinstance(build_engine(make_settings(tmp_path, runtime="replay")), ReplayEngine)


def test_the_scripts_and_the_allowlist_ship_with_the_package() -> None:
    """Without them an image would have the adapter and nothing to play — the
    same packaging hole the pi extension fell through once."""
    assert SCRIPTS_DIR.is_dir() and REHEARSAL_ALLOWLIST.is_file()
    packaging = (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text(encoding="utf-8")
    assert "replays/*" in packaging


def test_every_script_is_well_formed_and_one_is_the_default() -> None:
    scripts = load_scripts()
    assert len(scripts) >= 2
    assert sum(1 for script in scripts if script.get("default")) == 1
    for script in scripts:
        assert script["steps"], script["name"]
        assert "rehearsal" in script["report"].lower(), f"{script['name']} must say what it is"
        assert "rehearsal" in script["follow_up"].lower()


def test_a_script_is_chosen_on_the_title_line_only() -> None:
    scripts = load_scripts()
    assert choose(scripts, DISK)["name"] == "disk-fills"
    assert choose(scripts, GATEWAY)["default"] is True
    # A keyword in the BODY must not pick a script: the body is the alert's text.
    smuggled = "Title: Payment gateway 5xx\nBody: disk disk disk /data"
    assert choose(scripts, smuggled)["default"] is True
    assert choose(scripts, "no title line at all")["default"] is True


# ----------------------------------------------------- the five obligations


def test_the_session_id_is_first_and_survives_a_boot(tmp_path: Path) -> None:
    result, seen = _drive(_engine(tmp_path), DISK, "probe:inbound:1")
    assert seen[0]["type"] == "session"
    assert seen[0]["id"] == result.session_id == session_id_for("probe:inbound:1")
    # Derived from the key: a second process resuming this run lands on it.
    assert session_id_for("probe:inbound:1") == seen[0]["id"]
    assert session_id_for("probe:inbound:2") != seen[0]["id"]


def test_the_stream_is_tool_pairs_then_text(tmp_path: Path) -> None:
    _, seen = _drive(_engine(tmp_path), DISK, "probe:inbound:1")
    kinds = [event["type"] for event in seen]
    assert kinds[0] == "session" and kinds[-1] == "text"
    pairs = kinds[1:-1]
    assert pairs[::2] == ["tool_use"] * (len(pairs) // 2) and pairs[1::2] == ["tool_done"] * (len(pairs) // 2)
    for use, done in zip(seen[1:-1:2], seen[2:-1:2], strict=True):
        assert use["id"] == done["id"]
        assert use["detail"], "a step with no detail is a step the feed cannot show"


def test_the_refused_call_is_refused_by_the_real_gate_and_recorded(tmp_path: Path) -> None:
    """Each script carries exactly one call the read-only guard refuses. The
    refusal has to be the gate's — not a flag in the script — or the demo would
    be describing the boundary rather than showing it."""
    engine = _engine(tmp_path)
    for message, key in ((DISK, "probe:inbound:1"), (GATEWAY, "probe:inbound:2")):
        result, seen = _drive(engine, message, key)
        refused = [event for event in seen if event["type"] == "tool_done" and event.get("error")]
        assert len(refused) == 1, key
        assert result.guard_trips == 1
        lines = [
            json.loads(line)
            for path in (tmp_path / "audit").glob("*.jsonl")
            for line in path.read_text(encoding="utf-8").splitlines()
            if f'"session": "{key}"' in line
        ]
        denied = [line for line in lines if line.get("denied")]
        assert len(denied) == 1 and denied[0]["guard"] == "bash"
        assert all(line.get("replayed") for line in lines), "a replayed line must never pass for a live one"
        assert audit.consulted(tmp_path / "audit", key, since=0)
        assert audit.trips(tmp_path / "audit", key, since=0) == 1


def test_a_danger_only_posture_refuses_nothing_the_scripts_do(tmp_path: Path) -> None:
    """The posture is the deployment's, not the script's: the same script under
    `danger-only` records the same calls with a different verdict."""
    from hookprobe.guard import DANGER_ONLY

    result, seen = _drive(_engine(tmp_path, bash_guard=DANGER_ONLY), GATEWAY, "probe:inbound:3")
    refused = [event for event in seen if event["type"] == "tool_done" and event.get("error")]
    # A rollback is not on the danger list, so the wider posture lets it through
    # — which is the honest reading of that posture, and why no rehearsal node
    # is ever deployed under it.
    assert refused == [] and result.guard_trips == 0


def test_free_means_nothing_was_spent(tmp_path: Path) -> None:
    result, _ = _drive(_engine(tmp_path), DISK, "probe:inbound:1")
    assert result.cost_usd == 0.0
    assert result.usage == {"input_tokens": 0, "output_tokens": 0}, "a zero is free only with nothing behind it"
    assert result.error is None and result.duration_ms is not None


def test_a_resume_plays_the_follow_up_and_no_tools(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    first, _ = _drive(engine, DISK, "probe:inbound:1")
    second, seen = _drive(engine, "Why do you believe that?", "probe:inbound:1", resume=first.session_id)
    assert second.session_id == first.session_id
    assert [event["type"] for event in seen] == ["session", "text"]
    assert "rehearsal" in second.text.lower()


def test_a_stop_ends_the_turn_and_it_still_reports(tmp_path: Path) -> None:
    engine = ReplayEngine(make_settings(tmp_path, runtime="replay"), pace=0.05)

    async def scenario() -> Any:
        assert await engine.stop() is False, "nothing to stop yet"
        turn = asyncio.ensure_future(engine.run(message=DISK, session_key="probe:inbound:1"))
        await asyncio.sleep(0.08)
        assert await engine.stop() is True
        return await turn

    result = asyncio.run(scenario())
    assert result.error == "stopped" and "stopped" in result.text


def test_describe_inputs_says_what_it_is(tmp_path: Path) -> None:
    described = _engine(tmp_path).describe_inputs()
    assert described["runtime"] == "replay" and described["rehearsal"] is True
    assert described["posture"]["bash_guard"] == "readonly"
    assert "disk-fills" in described["scripts"]


# --------------------------------------- the loop the rehearsal exists to show


def test_the_proposal_it_makes_is_one_its_own_allowlist_admits() -> None:
    """The quickstart arms the rehearsal with `rehearsal-allowlist`. The steps
    the disk script proposes must full-match it, or the demo's approve press
    answers "refused" — which is a true answer about a broken demo."""
    patterns = remediation.allowlist_patterns(REHEARSAL_ALLOWLIST)
    assert patterns, "the shipped allowlist is empty"
    for script in load_scripts():
        steps = remediation.extract(script["report"])
        if script["name"] == "disk-fills":
            assert len(steps) == 2
            for step in steps:
                assert step["risk"] == "low", "a rehearsal proposes only observations"
                assert remediation.step_deny_reason(step, patterns, []) is None, step["command"]
                assert remediation.argv_for(step["command"])[0] is not None, "no shell, ever"
        else:
            assert steps == [], f"{script['name']} must propose nothing"


def test_through_the_service_a_rehearsal_completes_free_and_parks_a_proposal(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, runtime="replay", remediation_allowlist=REHEARSAL_ALLOWLIST)
    engine = ReplayEngine(settings, pace=0.0)

    async def scenario() -> None:
        service = RunService(settings, engine, RunStore(tmp_path / "results"))
        run = service.start({"message": DISK, "sessionKey": "probe:inbound:9"}, origin="relay")
        for _ in range(500):
            await asyncio.sleep(0.01)
            current = service.get(run.session_key)
            if current is not None and current.finished:
                break
        done = service.get("probe:inbound:9")
        assert done is not None and done.status == COMPLETED
        assert done.cost_usd == 0.0
        assert done.engine_session_id == session_id_for("probe:inbound:9")
        assert done.turns[0]["guard_trips"] == 1
        assert [e["type"] for e in done.events].count("tool_use") == 6
        proposal = remediation.load(tmp_path, done.meta["remediation_proposal"])
        assert proposal is not None and proposal["status"] == "proposed"
        assert [step["command"] for step in proposal["steps"]] == ["df -h /data", "du -sh /data/results"]

    asyncio.run(scenario())


@pytest.mark.parametrize("name", ["disk-fills", "service-errors"])
def test_every_script_names_a_real_refusal(name: str) -> None:
    """The refused call is a recorded fact in each script, and the report says
    the guard refused it — the two must agree, or the report lies about the
    audit beside it."""
    script = next(script for script in load_scripts() if script["name"] == name)
    assert "refused by the read-only guard" in script["report"]
    assert sum(1 for step in script["steps"] if "refused" in str(step.get("output", ""))) == 1
