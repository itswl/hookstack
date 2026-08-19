"""The investigator stays read-only; remediation runs only what an operator
approved, only what an allowlist permits, and writes down every command."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from hookprobe import remediation

REPORT = """Root cause: the cache is stale.

```remediation
[{"action": "clear the stale cache", "command": "redis-cli -h cache flushdb", "target": "cache",
  "risk": "medium", "rollback": "the cache refills from source on the next request"}]
```
"""


def test_extract_reads_the_block_and_validates_each_step() -> None:
    steps = remediation.extract(REPORT)
    assert len(steps) == 1
    assert steps[0]["command"] == "redis-cli -h cache flushdb"
    assert steps[0]["risk"] == "medium"
    # A step without a command or action is dropped, not defaulted.
    assert remediation.extract('```remediation\n[{"action": "x"}]\n```') == []
    assert remediation.extract("no block here") == []
    assert remediation.extract("```remediation\nnot json\n```") == []


def test_the_allowlist_denies_by_default_and_allows_by_full_match(tmp_path: Path) -> None:
    assert remediation.deny_reason("anything", []) is not None, "no allowlist = nothing runs"
    patterns = [r"redis-cli -h cache flushdb", r"kubectl rollout restart deploy/\w+ -n prod"]
    assert remediation.deny_reason("redis-cli -h cache flushdb", patterns) is None
    assert remediation.deny_reason("kubectl rollout restart deploy/api -n prod", patterns) is None
    # Full match, not search: a prefix that is on the list does not license a suffix.
    assert remediation.deny_reason("redis-cli -h cache flushdb; rm -rf /", patterns) is not None
    # A broken pattern fails closed.
    assert remediation.deny_reason("x", ["(unterminated"]) is not None


def _service(tmp_path, allowlist=None):
    from hookprobe.engine import EngineResult
    from hookprobe.runs import RunStore
    from hookprobe.service import RunService
    from tests.helpers import FakeEngine, make_settings

    settings = make_settings(tmp_path, remediation_allowlist=allowlist)
    engine = FakeEngine(result=EngineResult(text=REPORT, message_count=1))
    return RunService(settings, engine, RunStore(tmp_path / "results")), settings


def test_a_report_parks_a_proposal_but_nothing_runs_without_approval(tmp_path):
    async def scenario():
        service, _ = _service(tmp_path)
        service.start({"message": "Title: t\ngo", "sessionKey": "k1"})
        for _ in range(300):
            run = service.get("k1")
            if run and run.finished:
                return run, service
            await asyncio.sleep(0.01)
        raise AssertionError("never finished")

    run, service = asyncio.run(scenario())
    pid = run.meta["remediation_proposal"]
    row = remediation.load(tmp_path, pid)
    assert row["status"] == "proposed" and not row["results"]
    # The block stays in the report (unlike memory markers) — it is advice a
    # human reading the case wants.
    assert "```remediation" in run.text


def test_approval_without_an_allowlist_is_refused(tmp_path):
    async def scenario():
        service, _ = _service(tmp_path, allowlist=None)
        service.start({"message": "Title: t\ngo", "sessionKey": "k1"})
        for _ in range(300):
            run = service.get("k1")
            if run and run.finished:
                return run, service
            await asyncio.sleep(0.01)
        raise AssertionError("never finished")

    run, service = asyncio.run(scenario())
    with pytest.raises(PermissionError):
        service.approve_remediation(run.meta["remediation_proposal"])
    assert remediation.load(tmp_path, run.meta["remediation_proposal"])["status"] == "proposed"


def test_an_approved_allowlisted_command_runs_and_is_audited(tmp_path):
    allow = tmp_path / "allow.txt"
    allow.write_text("echo .*\n")
    report = 'ok\n```remediation\n[{"action":"probe","command":"echo remediated","risk":"low"}]\n```\n'

    async def scenario():
        from hookprobe.engine import EngineResult
        from hookprobe.runs import RunStore
        from hookprobe.service import RunService
        from tests.helpers import FakeEngine, make_settings

        settings = make_settings(tmp_path, remediation_allowlist=allow)
        engine = FakeEngine(result=EngineResult(text=report, message_count=1))
        service = RunService(settings, engine, RunStore(tmp_path / "results"))
        service.start({"message": "Title: t\ngo", "sessionKey": "k1"})
        for _ in range(300):
            run = service.get("k1")
            if run and run.finished:
                break
            await asyncio.sleep(0.01)
        pid = run.meta["remediation_proposal"]
        service.approve_remediation(pid)
        for _ in range(300):
            row = remediation.load(tmp_path, pid)
            if row["status"] in ("executed", "failed"):
                return row
            await asyncio.sleep(0.01)
        raise AssertionError("never executed")

    row = asyncio.run(scenario())
    assert row["status"] == "executed"
    assert row["results"][0]["exit"] == 0
    assert "remediated" in row["results"][0]["output"]
    audit = list((tmp_path / "audit").glob("*.jsonl"))
    assert audit and "echo remediated" in audit[0].read_text()


def test_a_failing_step_stops_the_sequence(tmp_path):
    allow = tmp_path / "allow.txt"
    allow.write_text(".*\n")  # permissive, so the STOP behaviour is what's tested
    report = (
        "x\n```remediation\n["
        '{"action":"a","command":"false","risk":"low"},'
        '{"action":"b","command":"echo should-not-run","risk":"low"}]\n```\n'
    )

    async def scenario():
        from hookprobe.engine import EngineResult
        from hookprobe.runs import RunStore
        from hookprobe.service import RunService
        from tests.helpers import FakeEngine, make_settings

        settings = make_settings(tmp_path, remediation_allowlist=allow)
        engine = FakeEngine(result=EngineResult(text=report, message_count=1))
        service = RunService(settings, engine, RunStore(tmp_path / "results"))
        service.start({"message": "Title: t\ngo", "sessionKey": "k1"})
        for _ in range(300):
            run = service.get("k1")
            if run and run.finished:
                break
            await asyncio.sleep(0.01)
        pid = run.meta["remediation_proposal"]
        service.approve_remediation(pid)
        for _ in range(300):
            row = remediation.load(tmp_path, pid)
            if row["status"] in ("executed", "failed"):
                return row
            await asyncio.sleep(0.01)
        raise AssertionError("never executed")

    row = asyncio.run(scenario())
    assert row["status"] == "failed"
    assert len(row["results"]) == 1, "the second command never ran"


def _approved(tmp_path, report: str, allow: str):
    """A service whose one proposal has been approved and is running."""
    from hookprobe.engine import EngineResult
    from hookprobe.runs import RunStore
    from hookprobe.service import RunService
    from tests.helpers import FakeEngine, make_settings

    allowlist = tmp_path / "allow.txt"
    allowlist.write_text(allow)
    settings = make_settings(tmp_path, remediation_allowlist=allowlist)
    engine = FakeEngine(result=EngineResult(text=report, message_count=1))
    return RunService(settings, engine, RunStore(tmp_path / "results")), settings


async def _finish(service, key: str):
    for _ in range(300):
        run = service.get(key)
        if run and run.finished:
            return run
        await asyncio.sleep(0.01)
    raise AssertionError("never finished")


def test_shutdown_waits_for_a_procedure_instead_of_stranding_it(tmp_path):
    """A process that went away mid-sequence left the row saying `running` — a
    state neither approve nor reject will touch, so the remaining steps were
    unrun and nothing anywhere said so."""
    report = (
        "x\n```remediation\n["
        '{"action":"a","command":"sleep 0.3 && echo first","risk":"low"},'
        '{"action":"b","command":"echo second","risk":"low"}]\n```\n'
    )

    async def scenario():
        service, _ = _approved(tmp_path, report, ".*\n")
        service.start({"message": "Title: t\ngo", "sessionKey": "k1"})
        run = await _finish(service, "k1")
        pid = run.meta["remediation_proposal"]
        service.approve_remediation(pid)
        assert remediation.load(tmp_path, pid)["status"] == "running"

        cancelled = await service.shutdown(grace_seconds=5.0)

        return remediation.load(tmp_path, pid), cancelled

    row, cancelled = asyncio.run(scenario())
    assert cancelled == 0, "the procedure was given the chance to finish, not cut off"
    assert row["status"] == "executed"
    assert [result["command"] for result in row["results"]] == ["sleep 0.3 && echo first", "echo second"]


def test_a_restart_settles_a_procedure_it_died_in_the_middle_of(tmp_path):
    """`running` is a state only the executing task can leave, so the boot sweep
    has to — recording which commands landed, because that is what an operator
    needs before touching the target again."""
    import json

    from fastapi.testclient import TestClient

    from hookprobe.app import create_app
    from hookprobe.runs import RunStore
    from hookprobe.service import RunService
    from tests.helpers import FakeEngine, make_settings

    directory = tmp_path / "remediation"
    directory.mkdir()
    (directory / "a1b2c3d4e5.json").write_text(
        json.dumps(
            {
                "id": "a1b2c3d4e5",
                "session_key": "probe:inbound:7",
                "created_at": 1.0,
                "status": "running",
                "steps": [
                    {"action": "drain", "command": "kubectl drain node-3", "risk": "high", "rollback": "uncordon"},
                    {"action": "restart", "command": "kubectl rollout restart deploy/api", "risk": "medium"},
                    {"action": "verify", "command": "curl -sf http://api/healthz", "risk": "low"},
                ],
                "results": [{"command": "kubectl drain node-3", "exit": 0, "ms": 12, "output": "node drained"}],
            }
        ),
        encoding="utf-8",
    )

    settings = make_settings(tmp_path, token="t")
    service = RunService(settings, FakeEngine(), RunStore(tmp_path / "results"))
    auth = {"Authorization": "Bearer t"}
    with TestClient(create_app(settings, service)) as client:
        rows = client.get("/v1/remediations", headers=auth).json()["proposals"]
        # Terminal now, so the row is one an operator can act on the truth of.
        assert client.post("/v1/remediations/a1b2c3d4e5/approve", headers=auth).status_code == 409

    assert len(rows) == 1
    assert rows[0]["status"] == "failed"
    assert rows[0]["interrupted"]["ran"] == ["kubectl drain node-3"]
    assert rows[0]["interrupted"]["not_run"] == [
        "kubectl rollout restart deploy/api",
        "curl -sf http://api/healthz",
    ]


def test_a_row_that_cannot_be_written_after_execution_is_loud(tmp_path, monkeypatch, caplog):
    """The commands have already run by then, so a lost write is not a detail:
    without a line in the log, the only account of it would be the audit file
    nobody was told to read."""
    import logging

    report = 'ok\n```remediation\n[{"action":"probe","command":"echo done","risk":"low"}]\n```\n'
    real_save = remediation.save

    def failing_save(workdir, row):
        if row.get("status") in ("executed", "failed"):
            raise OSError("no space left on device")
        real_save(workdir, row)

    async def scenario():
        service, _ = _approved(tmp_path, report, "echo .*\n")
        service.start({"message": "Title: t\ngo", "sessionKey": "k1"})
        run = await _finish(service, "k1")
        pid = run.meta["remediation_proposal"]
        monkeypatch.setattr("hookprobe.service.remediation.save", failing_save)
        service.approve_remediation(pid)
        await service.shutdown(grace_seconds=5.0)
        return pid

    with caplog.at_level(logging.ERROR):
        pid = asyncio.run(scenario())

    assert "could not be written" in caplog.text
    audit = list((tmp_path / "audit").glob("*.jsonl"))
    assert audit and "echo done" in audit[0].read_text(), "the flight recorder still has the command"
    # The row is left claiming to run, which is exactly what the boot sweep is for.
    assert remediation.load(tmp_path, pid)["status"] == "running"
    monkeypatch.setattr("hookprobe.service.remediation.save", real_save)
    assert remediation.settle_interrupted(tmp_path)[0]["status"] == "failed"


def test_the_proposal_dir_is_on_the_input_guard(tmp_path):
    from hookprobe import inputs

    assert inputs.write_deny_reason("remediation/x.json", workdir=tmp_path) is not None
