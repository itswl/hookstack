"""Investigations propose memory; only a person writes it."""

from __future__ import annotations

import asyncio
from pathlib import Path

from hookprobe import suggestions


def test_markers_are_lifted_out_of_the_report_and_capped() -> None:
    report = (
        "Root cause: the batch job.\n"
        "MEMORY-SUGGESTION: gateway-2's Sunday spike is the reporting batch job\n"
        "More prose.\n"
        "MEMORY-SUGGESTION: gateway-2's Sunday spike is the reporting batch job\n"
        "MEMORY-SUGGESTION: db-1 /data and / are one filesystem\n"
        "MEMORY-SUGGESTION: three\nMEMORY-SUGGESTION: four\n"
    )
    stripped, facts = suggestions.extract(report)
    assert "MEMORY-SUGGESTION" not in stripped
    assert "Root cause: the batch job." in stripped and "More prose." in stripped
    assert facts[0].startswith("gateway-2") and len(facts) == 3, "deduped, capped at 3"

    # And it says so where the lines were. Removing them silently left a bare
    # heading in the first self-review patrol's report, which reads identically
    # to a model that ignored the instruction — it cost a wrong diagnosis.
    assert "3 memory suggestions queued" in stripped
    assert stripped.count("queued for review") == 1, "one note per report, not one per marker"
    # Placed where the first marker was, so it stays under the run's own heading.
    assert stripped.index("queued for review") < stripped.index("More prose.")


def test_a_report_that_proposes_nothing_is_left_exactly_as_written() -> None:
    """The note must not appear when there was nothing to lift, or every report
    would claim a queued suggestion it never made."""
    report = "Root cause: the batch job.\n\nNothing qualifies for memory this week.\n"
    stripped, facts = suggestions.extract(report)

    assert facts == []
    assert stripped == report, "untouched, trailing newline and all"
    assert "queued" not in stripped


def test_accept_appends_under_one_heading_and_closes_the_row(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text("# env\n\nhand-written fact\n")
    suggestions.append(tmp_path, "probe:x:1", ["db-1 /data and / are one filesystem"])

    row = suggestions.load(tmp_path)[0]
    resolved = suggestions.resolve(tmp_path, row["id"], accept=True)

    assert resolved is not None and resolved["status"] == "accepted"
    memory = (tmp_path / "CLAUDE.md").read_text()
    assert "hand-written fact" in memory, "the operator's own text survives"
    assert suggestions.HEADING in memory
    assert "- db-1 /data and / are one filesystem" in memory
    assert [r["status"] for r in suggestions.load(tmp_path)] == ["accepted"]
    assert suggestions.resolve(tmp_path, row["id"], accept=True) is None, "a closed row stays closed"


def test_dismiss_touches_nothing_but_the_row(tmp_path: Path) -> None:
    suggestions.append(tmp_path, "probe:x:1", ["noise"])
    row = suggestions.load(tmp_path)[0]
    suggestions.resolve(tmp_path, row["id"], accept=False)
    assert not (tmp_path / "CLAUDE.md").exists()


def test_a_full_queue_refuses_rather_than_evicts(tmp_path: Path) -> None:
    for index in range(60):
        suggestions.append(tmp_path, f"s{index}", [f"fact {index}"])
    open_rows = [r for r in suggestions.load(tmp_path) if r["status"] == "open"]
    assert len(open_rows) == 50
    assert open_rows[0]["line"] == "fact 0", "the unread head of the queue is never evicted"


def test_the_service_lifts_suggestions_and_the_agent_cannot_write_the_queue(tmp_path: Path) -> None:
    from hookprobe import inputs
    from hookprobe.engine import EngineResult
    from hookprobe.runs import RunStore
    from hookprobe.service import RunService
    from tests.helpers import FakeEngine, make_settings

    async def scenario():
        engine = FakeEngine(
            result=EngineResult(text="Fine.\nMEMORY-SUGGESTION: the staging cluster is called demo-cn\n")
        )
        service = RunService(make_settings(tmp_path), engine, RunStore(tmp_path / "results"))
        service.start({"message": "Title: t\ngo", "sessionKey": "k1"})
        for _ in range(300):
            run = service.get("k1")
            if run is not None and run.finished:
                return run
            await asyncio.sleep(0.01)
        raise AssertionError("never finished")

    run = asyncio.run(scenario())
    assert "MEMORY-SUGGESTION" not in run.text, "channels never see the marker"
    assert run.meta["memory_suggestions"] == 1
    assert suggestions.load(tmp_path)[0]["line"] == "the staging cluster is called demo-cn"
    # And the write path the agent would need is on the input guard's list.
    assert inputs.write_deny_reason("memory-suggestions.jsonl", workdir=tmp_path) is not None
