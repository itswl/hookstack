"""The page-as-editor contract: file stays truth, PUT validates-or-changes-nothing."""

from __future__ import annotations

import json

import httpx
import pytest

from hookrelay.app import create_app
from hookrelay.settings import Settings

GOOD_YAML = """
sources:
  - name: test
    secret: ""
    title: "{title}"
channels:
  - name: sink
    type: generic
    url: https://sink.example/in
routes:
  - name: all
    source: "*"
    send_to: [sink]
"""

BETTER_YAML = (
    GOOD_YAML
    + """
  - name: extra
    source: "*"
    send_to: [sink]
    priority: 5
"""
)


@pytest.fixture
async def file_client(tmp_path):
    """An app that really loads its config from a file, like production."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(GOOD_YAML, encoding="utf-8")
    settings = Settings(
        config_path=str(config_path),
        db_path=str(tmp_path / "t.db"),
        plugins_dir=str(tmp_path / "none"),
        admin_token="admin-t",
        read_token="",
        max_body_bytes=256 * 1024,
        max_attempts=3,
        worker_interval_seconds=0.01,
    )
    app = create_app(settings=settings)
    async with (
        httpx.ASGITransport(app=app) as transport,
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://t") as client,
    ):
        client.config_path = config_path  # type: ignore[attr-defined]
        yield client


ADMIN = {"X-Admin-Token": "admin-t"}


async def test_get_config_returns_raw_yaml_admin_only(file_client):
    assert (await file_client.get("/config")).status_code == 403
    response = await file_client.get("/config", headers=ADMIN)
    assert response.status_code == 200
    assert "sources:" in response.json()["yaml"]


async def test_put_config_hot_applies_and_persists(file_client):
    response = await file_client.put("/config", content=BETTER_YAML, headers=ADMIN)
    assert response.status_code == 200
    assert response.json()["routes"] == ["extra", "all"]  # priority order

    # Hot: the new route participates immediately, no restart.
    hooked = await file_client.post("/hook/test", json={"title": "after edit"})
    assert hooked.json()["outcome"] == "routed"

    # Persisted: the FILE carries the edit (git-able source of truth).
    assert "extra" in file_client.config_path.read_text()


async def test_put_invalid_config_changes_nothing(file_client):
    bad = GOOD_YAML.replace("type: generic", "type: sorcery")
    response = await file_client.put("/config", content=bad, headers=ADMIN)
    assert response.status_code == 400
    assert "sorcery" in response.json()["detail"]
    # Old config still serving, file untouched.
    assert "sorcery" not in file_client.config_path.read_text()
    hooked = await file_client.post("/hook/test", json={"title": "still alive"})
    assert hooked.json()["outcome"] == "routed"


async def test_reload_picks_up_hand_edits(file_client):
    file_client.config_path.write_text(BETTER_YAML, encoding="utf-8")
    response = await file_client.post("/config/reload", headers=ADMIN)
    assert response.status_code == 200
    assert response.json()["routes"] == ["extra", "all"]


async def test_dead_delivery_can_be_requeued(file_client, monkeypatch):
    import hookrelay.delivery as delivery_mod
    from hookrelay.delivery import process_due

    hooked = await file_client.post("/hook/test", json={"title": json.dumps("x")})
    assert hooked.json()["outcome"] == "routed"

    async def always_fail(client, channel, message):
        return False, "http 500: nope"

    monkeypatch.setattr(delivery_mod.channels, "send", always_fail)
    # Ride to dead (max_attempts=3) by advancing the clock far each round.
    store = None
    # reach into the app the fixture built
    transport_app = file_client._transport.app  # type: ignore[attr-defined]
    store = transport_app.state.store
    cfg = transport_app.state.config
    settings = transport_app.state.settings
    import time

    now = time.time()  # enqueue used the real clock; the drain must too
    for i in range(4):
        await process_due(store, cfg, settings, object(), now=now + i * 10_000)
    counts = await store.queue_counts()
    assert counts["dead"] == 1

    delivery_id = (await store.recent_events(1))[0]["deliveries"][0]["id"]
    assert (await file_client.post(f"/deliveries/{delivery_id}/retry")).status_code == 403
    response = await file_client.post(f"/deliveries/{delivery_id}/retry", headers=ADMIN)
    assert response.status_code == 200
    assert (await store.queue_counts())["queued"] == 1
