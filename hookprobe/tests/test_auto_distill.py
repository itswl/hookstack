"""A finished run leaves the next one a runbook — written by the service.

The agent's own Write and Edit cannot reach `.claude/` (tests/test_inputs.py).
These tests are the other half of that decision: the loop still closes, and it
closes on the terms in distill.py's docstring — create-only, never from a run
that misbehaved, capped, and stamped.
"""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from hookprobe import distill


def run_record(**overrides):
    values = {
        "session_key": "hookprobe:abc",
        "run_id": "deadbeef",
        "engine_session_id": "sess-1",
        "model": "deepseek-chat",
        "origin": "relay",
        "error": None,
        "text": "Disk on db-1 filled up.",
        "current_message": "Investigate: disk pressure on db-1",
        "turns": [
            {
                "message": "Investigate: disk pressure on db-1",
                "text": "Disk on db-1 filled up.",
                "events": [
                    {"type": "tool_use", "name": "Bash", "detail": "df -h"},
                    {"type": "tool_use", "name": "Bash", "detail": "du -xh /var | sort -h | tail"},
                ],
            }
        ],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture()
def skills(tmp_path: Path) -> Path:
    path = tmp_path / ".claude" / "skills"
    path.mkdir(parents=True)
    return path


def install(skills: Path, run=None, limit: int = 10, input_changes=(), max_cases: int = 5):
    return distill.auto_write(
        run if run is not None else run_record(),
        skills_dir=skills,
        limit=limit,
        max_cases=max_cases,
        input_changes=input_changes,
    )


def test_a_finished_run_leaves_a_runbook(skills: Path) -> None:
    outcome = install(skills)

    name = outcome["installed"]
    manifest = (skills / name / "SKILL.md").read_text()
    assert f"name: {name}" in manifest
    assert "df -h" in manifest, "the tool sequence is the point of the runbook"
    assert "Disk on db-1 filled up." in manifest


def test_the_runbook_says_nobody_reviewed_it(skills: Path) -> None:
    """It is read by the next investigation, which cannot ask where it came from."""
    name = install(skills)["installed"]
    manifest = (skills / name / "SKILL.md").read_text()

    assert "reviewed by" in manifest and "nobody" in manifest
    assert "before saving" not in manifest, "that caveat is addressed to an operator who never saw this"
    assert f"DELETE /v1/skills/{name}" in manifest


def test_provenance_is_recorded_as_data(skills: Path) -> None:
    name = install(skills)["installed"]
    origin = json.loads((skills / name / "origin.json").read_text())

    assert origin["written_by"] == "auto-distill"
    assert origin["reviewed"] is False
    assert origin["session_key"] == "hookprobe:abc"
    assert origin["engine_session_id"] == "sess-1"


def test_a_second_investigation_adds_to_the_runbook_instead_of_replacing_it(skills: Path) -> None:
    """Self-update means accumulate. Replacing would be regression, not learning:
    a shallow run would flatten a runbook that had already seen five incidents,
    because a run only knows its own steps."""
    name = install(skills)["installed"]

    outcome = install(
        skills,
        run=run_record(
            session_key="hookprobe:def",
            text="Same disk, this time it was the WAL.",
            turns=[
                {
                    "message": "Investigate: disk pressure on db-1",
                    "text": "Same disk, this time it was the WAL.",
                    "events": [{"type": "tool_use", "name": "Bash", "detail": "ls -la /var/lib/postgresql"}],
                }
            ],
        ),
    )

    assert outcome == {"updated": name}
    manifest = (skills / name / "SKILL.md").read_text()
    assert "Same disk, this time it was the WAL." in manifest
    assert "Disk on db-1 filled up." in manifest, "the earlier case survives the update"
    assert manifest.index("the WAL") < manifest.index("filled up"), "newest first"


def test_an_operator_correction_survives_every_later_run(skills: Path) -> None:
    """Nothing here is restricted — but a write that ate a human's correction
    would restrict them in practice, by making corrections not worth typing."""
    name = install(skills)["installed"]
    manifest = skills / name / "SKILL.md"
    manifest.write_text(manifest.read_text() + "\n## Operator notes\n\nIgnore step 1, it is always noise.\n")

    install(skills, run=run_record(session_key="hookprobe:ghi"))

    assert "Ignore step 1, it is always noise." in manifest.read_text()


def test_every_write_leaves_the_previous_version_recoverable(skills: Path) -> None:
    """Why an update needs no permission check: nothing is lost."""
    name = install(skills)["installed"]
    before = (skills / name / "SKILL.md").read_text()

    install(skills, run=run_record(session_key="hookprobe:def"))

    kept = sorted((skills / name / "history").glob("*-SKILL.md"))
    assert len(kept) == 1
    assert kept[0].read_text() == before


def test_the_case_list_is_trimmed_but_the_rest_of_the_file_is_not(skills: Path) -> None:
    name = install(skills, max_cases=2)["installed"]
    for index in range(3):
        install(
            skills,
            max_cases=2,
            run=run_record(
                session_key=f"hookprobe:{index}",
                text=f"conclusion number {index}",
                turns=[{"message": "Investigate: disk pressure on db-1", "text": f"conclusion number {index}"}],
            ),
        )

    manifest = (skills / name / "SKILL.md").read_text()
    assert manifest.count("<!-- case:start") == 2
    assert "conclusion number 2" in manifest and "conclusion number 1" in manifest
    assert "conclusion number 0" not in manifest
    # The parts an update may not touch.
    assert manifest.startswith("---\nname: ")
    assert "How much to trust this" in manifest


def test_a_runbook_with_no_marker_is_appended_to_rather_than_guessed_at(skills: Path) -> None:
    """A hand-written runbook, or one from before the seam existed."""
    (skills / "hand-made").mkdir()
    manifest = skills / "hand-made" / "SKILL.md"
    manifest.write_text("---\nname: hand-made\n---\n\n# Written by a person\n\nDo not lose this.\n")

    merged = distill.merge_case(manifest.read_text(), "<!-- case:start 1 -->\nnew\n<!-- case:end -->\n", max_cases=5)

    assert "Do not lose this." in merged
    assert distill.CASES_MARKER in merged
    assert merged.index("Do not lose this.") < merged.index("new")


def test_a_run_that_changed_its_own_inputs_leaves_nothing(skills: Path) -> None:
    """It already misbehaved; it does not also get to leave instructions."""
    outcome = install(skills, input_changes=("created /data/.claude/skills/planted/SKILL.md",))

    assert outcome == {"skipped": "run changed its own inputs"}
    assert list(skills.iterdir()) == []


def test_a_failed_run_leaves_nothing(skills: Path) -> None:
    assert install(skills, run=run_record(error="engine reported error_max_turns")) == {"skipped": "run failed"}
    assert install(skills, run=run_record(text="   ")) == {"skipped": "run produced no report"}
    assert list(skills.iterdir()) == []


def test_the_cap_holds_for_new_runbooks_and_evicts_nothing(skills: Path) -> None:
    """Something here may have been reviewed; this is not the code that decides."""
    for index in range(3):
        (skills / f"existing-{index}").mkdir()
        (skills / f"existing-{index}" / "SKILL.md").write_text("kept\n")

    outcome = install(skills, limit=3)

    assert outcome == {"skipped": "at the 3-runbook cap"}
    assert sorted(path.name for path in skills.iterdir()) == ["existing-0", "existing-1", "existing-2"]


def test_the_cap_never_stops_a_runbook_from_going_on_learning(skills: Path) -> None:
    """The cap is prefix cost for a new entry; an update costs a case.
    Refusing that would be exactly the restriction this loop must not have."""
    name = install(skills, limit=1)["installed"]

    outcome = install(skills, limit=1, run=run_record(session_key="hookprobe:def"))

    assert outcome == {"updated": name}


def test_the_loop_is_off_unless_the_operator_sets_a_cap(tmp_path: Path, monkeypatch) -> None:
    """0 is not "unlimited"; it is the manual loop, and it is the default."""
    from hookprobe.settings import Settings

    monkeypatch.delenv("HOOKPROBE_AUTO_DISTILL_MAX", raising=False)
    monkeypatch.setenv("HOOKPROBE_TOKEN", "t")
    assert Settings.load().auto_distill_max == 0


# --- through the service, which is where the wiring can be wrong ------------


async def wait_finished(service, key: str, deadline: float = 3.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + deadline
    while loop.time() < end:
        run = service.get(key)
        if run is not None and run.finished:
            return run
        await asyncio.sleep(0.01)
    raise AssertionError("run did not finish in time")


def drive(tmp_path: Path, engine_result=None, **settings):
    """One run, start to finish, through the real service."""
    from hookprobe.engine import EngineResult
    from hookprobe.runs import RunStore
    from hookprobe.service import RunService
    from tests.helpers import FakeEngine, make_settings

    async def scenario():
        engine = FakeEngine(result=engine_result or EngineResult(text="Disk on db-1 filled up.", message_count=2))
        service = RunService(make_settings(tmp_path, **settings), engine, RunStore(tmp_path / "results"))
        service.start({"message": "Investigate: disk pressure on db-1", "sessionKey": "k1"})
        return await wait_finished(service, "k1")

    return asyncio.run(scenario())


def test_a_completed_run_installs_its_runbook_through_the_service(tmp_path: Path) -> None:
    run = drive(tmp_path, auto_distill_max=5)

    name = run.distilled["installed"]
    assert (tmp_path / ".claude" / "skills" / name / "SKILL.md").is_file()


def test_with_the_loop_off_nothing_is_written_and_nothing_is_claimed(tmp_path: Path) -> None:
    run = drive(tmp_path, auto_distill_max=0)

    assert run.distilled == {}
    assert not (tmp_path / ".claude" / "skills").exists()


def test_the_service_records_why_it_wrote_nothing(tmp_path: Path) -> None:
    """A loop that silently does nothing is the failure this feature ends."""
    from hookprobe.engine import EngineResult

    run = drive(
        tmp_path,
        engine_result=EngineResult(
            text="Disk on db-1 filled up.",
            input_changes=("created /data/.claude/skills/planted/SKILL.md",),
        ),
        auto_distill_max=5,
    )

    assert run.distilled == {"skipped": "run changed its own inputs"}


def test_a_recurring_alert_keeps_one_runbook(skills: Path) -> None:
    """The name comes from the question, so the same condition reuses its runbook
    rather than growing a new near-duplicate on every incident."""
    first = install(skills)
    second = install(skills, run=run_record(session_key="hookprobe:def", run_id="cafe"))

    assert "installed" in first
    assert "updated" in second
    assert len(list(skills.iterdir())) == 1


def test_an_operator_save_counts_as_the_review(tmp_path: Path) -> None:
    """Reviewed means somebody read it, which is exactly what a PUT is."""
    from fastapi.testclient import TestClient

    from hookprobe.app import create_app
    from hookprobe.runs import RunStore
    from hookprobe.service import RunService
    from tests.helpers import FakeEngine, make_settings

    settings = make_settings(tmp_path, auto_distill_max=5)
    client = TestClient(create_app(settings, RunService(settings, FakeEngine(), RunStore(tmp_path / "results"))))
    headers = {"Authorization": f"Bearer {settings.token}"}

    client.put("/v1/skills/db-latency", json={"content": "# first\n"}, headers=headers)
    client.put("/v1/skills/db-latency", json={"content": "# corrected\n"}, headers=headers)

    listed = {row["name"]: row for row in client.get("/v1/skills", headers=headers).json()}
    assert listed["db-latency"]["reviewed"] is True
    assert listed["db-latency"]["written_by"] == "operator"
    # And the version they replaced is still there.
    history = sorted((tmp_path / ".claude" / "skills" / "db-latency" / "history").glob("*-SKILL.md"))
    assert [path.read_text() for path in history] == ["# first\n"]
