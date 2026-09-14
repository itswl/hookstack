"""A synthetic run is marked once and kept out of the books everywhere.

Three of the twenty runbooks on the production shelf were distilled from test
and by-hand runs; a by-hand check of the re-fire gate counted on the weekly page
as a re-fire answered from a runbook; drill runs sat on the work board. Real
machinery on unreal work, so the marker is set where the run is created and
every reader — distiller, re-fire anchor, vouch, weekly page, board — leaves it
out. The tests are one per reader, because each one is a place the mark could
be forgotten.
"""

from __future__ import annotations

import asyncio
import time

from hookprobe import work
from hookprobe.app import _summary
from hookprobe.runs import COMPLETED, Run, RunStore
from hookprobe.service import RunService
from tests.helpers import FakeEngine, make_settings

TITLE = "queue-backlog"


def _service(tmp_path, **overrides):
    settings = make_settings(tmp_path, **overrides)
    return RunService(settings, FakeEngine(), RunStore(tmp_path / "results"))


async def _finished(service, key, deadline=3.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + deadline
    while loop.time() < end:
        run = service.get(key)
        if run is not None and run.finished:
            return run
        await asyncio.sleep(0.01)
    raise AssertionError("run did not finish")


def test_the_prefix_marks_the_run_and_the_summary_says_so(tmp_path):
    keys = (("manual:refire-check:1", True), ("drill:resume:2", True), ("probe:grafana:3", False))

    async def scenario():
        service = _service(tmp_path)
        runs = [service.start({"message": "x", "sessionKey": key, "_meta": {"title": TITLE}}) for key, _ in keys]
        for key, _ in keys:
            await _finished(service, key)
        await service.shutdown()
        return runs

    for run, (key, expected) in zip(asyncio.run(scenario()), keys, strict=True):
        assert bool(run.meta.get("synthetic")) is expected, key
        assert _summary(run)["synthetic"] is expected, key


def test_a_synthetic_run_distils_no_runbook(tmp_path):
    """The distiller is the reader that did the most damage: a shell-command
    test and a chat about a test environment both became runbooks loaded into
    every later investigation."""

    async def scenario():
        service = _service(tmp_path, auto_distill_max=5)
        service.start({"message": "x", "sessionKey": "manual:wiring-test:1", "_meta": {"title": TITLE}})
        synthetic = await _finished(service, "manual:wiring-test:1")
        service.start({"message": "x", "sessionKey": "probe:grafana:9", "_meta": {"title": TITLE}})
        real = await _finished(service, "probe:grafana:9")
        await service.shutdown()
        return synthetic, real

    synthetic, real = asyncio.run(scenario())
    assert synthetic.distilled == {"skipped": "a synthetic run teaches nothing"}
    assert "installed" in real.distilled or "updated" in real.distilled, real.distilled
    shelf = sorted(p.parent.name for p in (tmp_path / ".claude" / "skills").glob("*/SKILL.md"))
    assert len(shelf) == 1, "only the real run left a runbook"


def test_a_synthetic_run_anchors_no_re_fire_answer(tmp_path):
    """A drill of a real condition is a clean completed run of that title; the
    re-fire gate must still pay for the next real fire rather than answer from
    a rehearsal."""

    async def scenario():
        service = _service(tmp_path, refire_answer_hours=24)
        drill = Run(session_key="drill:x", run_id="d1", status=COMPLETED, text="rehearsed finding")
        drill.meta = {"title": TITLE, "level": "high", "source": "grafana", "synthetic": True}
        drill.created_at = time.time() - 3600
        drill.finished_at = time.time() - 3500
        service._store.create(drill)
        run = service.start(
            {
                "message": "alert",
                "sessionKey": "probe:grafana:2",
                "_meta": {"title": TITLE, "level": "high", "source": "grafana"},
            },
            origin="relay",
        )
        answered = bool(run.meta.get("answered_from_runbook"))
        if not answered:
            await _finished(service, "probe:grafana:2")
        await service.shutdown()
        return answered

    assert asyncio.run(scenario()) is False


def test_a_synthetic_run_vouches_for_nothing(tmp_path):
    service = _service(tmp_path, runbook_answer_days=7)
    drill = Run(session_key="drill:x", run_id="d1", status=COMPLETED, text="rehearsed")
    drill.meta = {"title": TITLE, "synthetic": True}
    drill.ruling, drill.ruled_at = "useful", time.time()
    service._store.create(drill)
    assert service._vouching_run(TITLE, time.time() - 86400) is None
    asyncio.run(service.shutdown())


def test_the_board_leaves_synthetic_runs_out(tmp_path):
    drill = Run(session_key="drill:x", run_id="d1", status=COMPLETED, text="rehearsed")
    drill.meta = {"title": TITLE, "synthetic": True}
    real = Run(session_key="probe:grafana:1", run_id="r1", status=COMPLETED, text="finding")
    real.meta = {"title": TITLE}
    items = work.resolve([drill, real], proposals=[], suggestions=[])
    assert [item.work_id for item in items] == ["probe:grafana:1"]
