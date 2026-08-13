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


def test_skills_filter_expression() -> None:
    from hookprobe.engine import _skills_filter

    assert _skills_filter("") is None
    assert _skills_filter("all") == "all"
    assert _skills_filter("a, b ,c") == ["a", "b", "c"]


def test_setting_sources_parse(monkeypatch) -> None:
    from hookprobe.settings import Settings

    monkeypatch.setenv("HOOKPROBE_SETTING_SOURCES", "user, project, bogus")
    assert Settings.load().setting_sources == ("user", "project")
    monkeypatch.setenv("HOOKPROBE_SETTING_SOURCES", "bogus,,")
    assert Settings.load().setting_sources == ("project",), "nonsense falls back to project"


def test_skills_browser_shows_only_loaded_layers(tmp_path, monkeypatch) -> None:
    """The browser mirrors the engine: the user layer appears exactly when
    the engine would load it."""
    from fastapi.testclient import TestClient

    from hookprobe.app import create_app
    from hookprobe.runs import RunStore
    from tests.helpers import FakeEngine

    workdir = tmp_path / "wd"
    home = tmp_path / "home"
    (workdir / ".claude" / "skills" / "distilled").mkdir(parents=True)
    (workdir / ".claude" / "skills" / "distilled" / "SKILL.md").write_text(
        "---\nname: distilled\ndescription: ours\n---\n"
    )
    (home / ".claude" / "skills" / "host-lib").mkdir(parents=True)
    (home / ".claude" / "skills" / "host-lib" / "SKILL.md").write_text(
        "---\nname: host-lib\ndescription: from the host\n---\n"
    )
    monkeypatch.setenv("HOME", str(home))

    def client_with(sources):
        settings = make_settings(workdir, token=TOKEN, setting_sources=sources)
        return TestClient(create_app(settings, RunService(settings, FakeEngine(), RunStore(workdir / "results"))))

    with client_with(("project",)) as client:
        names = {s["name"] for s in client.get("/v1/skills", headers=AUTH).json()}
        assert names == {"distilled"}, "user layer hidden while the engine would not load it"

    with client_with(("user", "project")) as client:
        listed = {s["name"]: s["layer"] for s in client.get("/v1/skills", headers=AUTH).json()}
        assert listed == {"distilled": "project", "host-lib": "user"}
        detail = client.get("/v1/skills/host-lib", headers=AUTH).json()
        assert detail["layer"] == "user" and "from the host" in detail["content"]


def test_skill_editing_is_copy_on_write_over_the_user_layer(tmp_path, monkeypatch) -> None:
    """Saving always lands in the project layer; deleting a shadow lets the
    host copy resurface; the host copy itself is never writable."""
    from fastapi.testclient import TestClient

    from hookprobe.app import create_app
    from hookprobe.runs import RunStore
    from tests.helpers import FakeEngine

    workdir = tmp_path / "wd"
    home = tmp_path / "home"
    host_skill = home / ".claude" / "skills" / "host-lib"
    host_skill.mkdir(parents=True)
    (host_skill / "SKILL.md").write_text("---\nname: host-lib\ndescription: from the host\n---\noriginal\n")
    (workdir / ".claude" / "skills").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    settings = make_settings(workdir, token=TOKEN, setting_sources=("user", "project"))
    service = RunService(settings, FakeEngine(), RunStore(workdir / "results"))
    with TestClient(create_app(settings, service)) as client:
        assert client.put("/v1/skills/bad name!", json={"content": "x"}, headers=AUTH).status_code == 400
        assert client.delete("/v1/skills/host-lib", headers=AUTH).status_code == 403

        saved = client.put(
            "/v1/skills/host-lib",
            json={"content": "---\nname: host-lib\ndescription: tuned\n---\nlocal copy\n"},
            headers=AUTH,
        ).json()
        assert saved["layer"] == "project"
        detail = client.get("/v1/skills/host-lib", headers=AUTH).json()
        assert detail["layer"] == "project" and "local copy" in detail["content"]
        assert "original" in (host_skill / "SKILL.md").read_text(), "the host copy was never touched"

        assert client.delete("/v1/skills/host-lib", headers=AUTH).json()["deleted"] is True
        resurfaced = client.get("/v1/skills/host-lib", headers=AUTH).json()
        assert resurfaced["layer"] == "user" and "original" in resurfaced["content"]

        assert client.delete("/v1/skills/host-lib", headers=AUTH).status_code == 403
        assert client.delete("/v1/skills/never-was", headers=AUTH).status_code == 404
