"""Operational surfaces: retention pruning and the health slot arithmetic."""

import json
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


def test_agents_config_and_prompt_append_loaders(tmp_path) -> None:
    from hookprobe.engine import _load_agents_raw, _system_prompt_append
    from tests.helpers import make_settings

    agents_file = tmp_path / "agents.json"
    agents_file.write_text(
        json.dumps(
            {
                "db-specialist": {"description": "DB 调查", "prompt": "你是数据库专家", "model": "inherit"},
                "broken": {"prompt": "no description"},
            }
        )
    )
    agents = _load_agents_raw(agents_file)
    assert set(agents) == {"db-specialist"}, "entries without description+prompt are dropped"
    assert agents["db-specialist"]["model"] == "inherit"
    assert _load_agents_raw(tmp_path / "missing.json") == {}

    # Convention path: {workdir}/system-prompt.md, read fresh, optional.
    settings = make_settings(tmp_path)
    assert _system_prompt_append(settings) == ""
    (tmp_path / "system-prompt.md").write_text("结论先行。\n")
    assert _system_prompt_append(settings) == "结论先行。"
    explicit = tmp_path / "sop.md"
    explicit.write_text("先取数再下结论")
    assert _system_prompt_append(make_settings(tmp_path, system_prompt_append=explicit)) == "先取数再下结论"


def test_audit_hook_writes_a_flight_record(tmp_path) -> None:
    import asyncio

    from hookprobe.engine import _audit_hook

    hook = _audit_hook(tmp_path / "audit", "probe:inbound:7")
    asyncio.run(
        hook(
            {"tool_name": "Bash", "tool_input": {"command": "df -h"}, "tool_response": {"is_error": False}},
            "tu_1",
            None,
        )
    )
    files = list((tmp_path / "audit").glob("*.jsonl"))
    assert len(files) == 1
    line = json.loads(files[0].read_text().strip())
    assert line["session"] == "probe:inbound:7"
    assert line["tool"] == "Bash" and line["detail"] == "df -h" and line["error"] is False


def test_mcp_loader_accepts_three_dialects(tmp_path) -> None:
    from hookprobe.engine import _load_mcp_servers

    bare = tmp_path / "bare.json"
    bare.write_text(json.dumps({"prom": {"command": "npx", "args": ["prometheus-mcp"], "env": {}}}))
    assert "prom" in _load_mcp_servers(bare)

    wrapped = tmp_path / "wrapped.json"
    wrapped.write_text(json.dumps({"mcpServers": {"grafana": {"command": "npx", "args": []}}}))
    assert "grafana" in _load_mcp_servers(wrapped)

    market = tmp_path / "market.json"
    market.write_text(
        json.dumps(
            {
                "on": {"enabled": True, "command": "npx", "args": ["x"], "env": {"TOKEN": "secret"}},
                "off": {"enabled": False, "command": "npx", "args": ["y"], "env": {}},
            }
        )
    )
    servers = _load_mcp_servers(market)
    assert set(servers) == {"on"}, "enabled:false entries are skipped"
    assert "enabled" not in servers["on"], "the marketplace flag never reaches the SDK"

    browsable = _load_mcp_servers(market, include_disabled=True)
    assert set(browsable) == {"on", "off"}, "the browser sees disabled entries too"
    assert browsable["off"]["enabled"] is False

    assert _load_mcp_servers(tmp_path / "missing.json") == {}
    assert _load_mcp_servers(None) == {}


def test_mcp_endpoint_redacts_env_values(tmp_path) -> None:
    from fastapi.testclient import TestClient

    from hookprobe.app import create_app
    from hookprobe.runs import RunStore
    from tests.helpers import FakeEngine

    config = tmp_path / "mcp.json"
    config.write_text(
        json.dumps({"prom": {"command": "npx", "args": ["prometheus-mcp"], "env": {"PROM_TOKEN": "hunter2"}}})
    )
    settings = make_settings(tmp_path, token=TOKEN, mcp_config=config)
    with TestClient(create_app(settings, RunService(settings, FakeEngine(), RunStore(tmp_path / "r")))) as client:
        body = client.get("/v1/mcp", headers=AUTH).json()
        assert body["servers"]["prom"]["command"] == "npx"
        assert body["servers"]["prom"]["env_keys"] == ["PROM_TOKEN"]
        assert "hunter2" not in json.dumps(body), "env values are secrets and never leave the file"


def _ops_client(tmp_path, monkeypatch, **overrides):
    from fastapi.testclient import TestClient

    from hookprobe.app import create_app
    from hookprobe.runs import RunStore
    from tests.helpers import FakeEngine

    workdir = tmp_path / "wd"
    workdir.mkdir(exist_ok=True)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    settings = make_settings(workdir, token=TOKEN, **overrides)
    return TestClient(create_app(settings, RunService(settings, FakeEngine(), RunStore(workdir / "r")))), workdir, home


def test_agents_endpoints_mirror_the_skills_story(tmp_path, monkeypatch) -> None:
    agents_config = tmp_path / "agents.json"
    agents_config.write_text(json.dumps({"pinned": {"description": "配置钉死的角色", "prompt": "p"}}))
    client, workdir, home = _ops_client(
        tmp_path, monkeypatch, setting_sources=("user", "project"), agents_config=agents_config
    )
    host_dir = home / ".claude" / "agents"
    host_dir.mkdir(parents=True)
    (host_dir / "host-role.md").write_text("---\nname: host-role\ndescription: from host\n---\nhost prompt\n")
    # Suppress the first-boot seeds: this test pins the exact listing.
    project_agents = workdir / ".claude" / "agents"
    project_agents.mkdir(parents=True, exist_ok=True)
    (project_agents / ".defaults-seeded").write_text("test")

    with client:
        listing = {a["name"]: a["source"] for a in client.get("/v1/agents", headers=AUTH).json()}
        assert listing == {"pinned": "config", "host-role": "user"}

        assert client.put("/v1/agents/pinned", json={"content": "x"}, headers=AUTH).status_code == 403
        assert client.delete("/v1/agents/host-role", headers=AUTH).status_code == 403

        saved = client.put(
            "/v1/agents/host-role",
            json={"content": "---\nname: host-role\ndescription: tuned\n---\nlocal prompt\n"},
            headers=AUTH,
        ).json()
        assert saved["source"] == "project"
        assert client.get("/v1/agents/host-role", headers=AUTH).json()["source"] == "project"
        assert "from host" in (host_dir / "host-role.md").read_text()

        assert client.delete("/v1/agents/host-role", headers=AUTH).json()["deleted"] is True
        assert client.get("/v1/agents/host-role", headers=AUTH).json()["source"] == "user"


def test_system_prompt_roundtrip_and_config_redaction(tmp_path, monkeypatch) -> None:
    client, workdir, _ = _ops_client(
        tmp_path, monkeypatch, alarm_url="https://open.feishu.cn/bot/hook/SECRET", event_secret="s1"
    )
    with client:
        assert client.get("/v1/system-prompt", headers=AUTH).json()["content"] == ""
        client.put("/v1/system-prompt", json={"content": "先取数再下结论。"}, headers=AUTH)
        assert (workdir / "system-prompt.md").read_text() == "先取数再下结论。"

        config = client.get("/v1/config", headers=AUTH).json()
        assert config["system_prompt"]["active"] is True
        assert config["alarm_configured"] is True and config["event_secret_set"] is True
        assert "SECRET" not in json.dumps(config), "secret-bearing URLs never leave the settings"


def test_audit_tail_filters_by_session(tmp_path, monkeypatch) -> None:
    client, workdir, _ = _ops_client(tmp_path, monkeypatch)
    audit = workdir / "audit"
    audit.mkdir()
    lines = [
        {"ts": 1.0, "session": "probe:inbound:1", "tool": "Bash", "detail": "df", "error": False},
        {"ts": 2.0, "session": "web:x", "tool": "Read", "detail": "/etc/hosts", "error": False},
        {"ts": 3.0, "session": "probe:inbound:1", "tool": "Grep", "detail": "err", "error": True},
    ]
    (audit / "2026-08-13.jsonl").write_text("\n".join(json.dumps(x) for x in lines))
    with client:
        everything = client.get("/v1/audit", headers=AUTH).json()
        assert everything["count"] == 3
        filtered = client.get("/v1/audit?session=inbound:1", headers=AUTH).json()
        assert [e["tool"] for e in filtered["entries"]] == ["Bash", "Grep"]
        capped = client.get("/v1/audit?limit=1", headers=AUTH).json()
        assert capped["count"] == 1 and capped["entries"][0]["ts"] == 3.0, "newest last, oldest dropped"


def test_default_agents_seed_once_and_respect_choices(tmp_path) -> None:
    from hookprobe.seeds import DEFAULT_AGENTS, seed_default_agents

    workdir = tmp_path / "wd"
    agents_dir = workdir / ".claude" / "agents"
    # An operator file that predates the seeding must never be clobbered.
    agents_dir.mkdir(parents=True)
    (agents_dir / "log-analyst.md").write_text("mine")

    written = seed_default_agents(workdir)
    assert written == len(DEFAULT_AGENTS) - 1
    assert (agents_dir / "log-analyst.md").read_text() == "mine"
    assert (agents_dir / "metrics-analyst.md").exists()
    assert (agents_dir / ".defaults-seeded").exists()

    # Deleting a seed sticks: the marker stops any re-seeding.
    (agents_dir / "metrics-analyst.md").unlink()
    assert seed_default_agents(workdir) == 0
    assert not (agents_dir / "metrics-analyst.md").exists()
