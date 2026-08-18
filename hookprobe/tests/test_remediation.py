"""The investigator stays read-only; remediation runs only what an operator
approved, only what an allowlist permits, and writes down every command."""

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


def test_the_proposal_dir_is_on_the_input_guard(tmp_path):
    from hookprobe import inputs

    assert inputs.write_deny_reason("remediation/x.json", workdir=tmp_path) is not None
