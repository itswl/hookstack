"""Operational surfaces: retention pruning and the health slot arithmetic."""

import os
import time

from fastapi.testclient import TestClient

from hookprobe.app import create_app
from hookprobe.retention import prune
from hookprobe.runs import RunStore
from hookprobe.service import RunService
from tests.helpers import GatedEngine, make_settings

TOKEN = "secret-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def test_prune_removes_only_old_files(tmp_path) -> None:
    workdir = tmp_path / "wd"
    home = tmp_path / "home"
    results = workdir / "results"
    results.mkdir(parents=True)
    transcripts = home / ".claude" / "projects" / "session-1"
    transcripts.mkdir(parents=True)

    old_result = results / "old.json"
    old_result.write_text("{}")
    new_result = results / "new.json"
    new_result.write_text("{}")
    old_transcript = transcripts / "t.jsonl"
    old_transcript.write_text("")
    stale = time.time() - 10 * 86400
    os.utime(old_result, (stale, stale))
    os.utime(old_transcript, (stale, stale))

    assert prune(workdir, home, 0) == 0  # disabled deletes nothing
    assert prune(workdir, home, 7) == 2
    assert new_result.exists()
    assert not old_result.exists()
    assert not old_transcript.exists()
    # Skills and memory live outside both roots — pruning never sees them.


def test_healthz_slot_arithmetic(tmp_path) -> None:
    engine = GatedEngine()
    settings = make_settings(tmp_path, token=TOKEN, max_concurrent=2)
    service = RunService(settings, engine, RunStore(tmp_path / "results"))
    with TestClient(create_app(settings, service)) as client:
        for i in range(3):
            client.post("/hooks/agent", json={"message": "m", "sessionKey": f"s:{i}"}, headers=AUTH)
        health = {}
        for _ in range(300):
            health = client.get("/healthz").json()
            if health.get("running_turns") == 2 and health.get("queued_turns") == 1:
                break
            time.sleep(0.01)
        assert (health["running_turns"], health["queued_turns"]) == (2, 1)
        engine.release.set()
