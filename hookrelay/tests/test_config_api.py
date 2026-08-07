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
        retention_days=14,
        alarm_url="",
        alarm_min_interval_seconds=600,
        breaker_threshold=5,
        breaker_cooldown_seconds=60,
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


# ── dry run and ledger search ────────────────────────────────────────────────


async def test_explain_answers_without_recording_or_delivering(file_client):
    """The explain button must never become a way to put a message in the
    group: no event, no delivery, no signature required (you are asking about
    a payload you hold, not delivering one)."""
    before = (await file_client.get("/status")).json()

    response = await file_client.post("/explain/test", json={"title": "would this route?"}, headers=ADMIN)
    assert response.status_code == 200
    body = response.json()
    assert body["dry_run"] is True
    assert body["outcome"] == "routed" and body["channels"] == ["sink"]
    assert [s["gate"] for s in body["steps"]] == ["extract", "dedup", "silence", "routes"]
    assert body["extracted"]["title"] == "would this route?"

    after = (await file_client.get("/status")).json()
    assert after["recent"] == before["recent"], "a dry run leaves no trace"
    assert after["queue"] == before["queue"]


async def test_explain_is_admin_gated_and_validates_input(file_client):
    assert (await file_client.post("/explain/test", json={})).status_code == 403
    assert (await file_client.post("/explain/nope", json={}, headers=ADMIN)).status_code == 404
    bad = await file_client.post("/explain/test", content=b"not json", headers=ADMIN)
    assert bad.status_code == 400


async def test_explain_reports_a_miss_as_clearly_as_a_hit(file_client):
    """Route the door to nothing, and the dry run must say WHY."""
    narrowed = GOOD_YAML.replace(
        'routes:\n  - name: all\n    source: "*"\n    send_to: [sink]',
        'routes:\n  - name: only-high\n    source: "*"\n    when: {level: [high]}\n    send_to: [sink]',
    )
    assert (await file_client.put("/config", content=narrowed, headers=ADMIN)).status_code == 200

    body = (await file_client.post("/explain/test", json={"title": "quiet"}, headers=ADMIN)).json()
    assert body["outcome"] == "skipped" and body["skip_code"] == "no_route"
    considered = body["steps"][-1]["considered"]
    assert considered[0]["matched"] is False and considered[0]["missed_on"] == ["level"]


async def test_ledger_search_filters_and_paginates(file_client):
    for i in range(6):
        await file_client.post("/hook/test", json={"title": f"事件 {i}" if i % 2 else f"other {i}"})

    all_events = (await file_client.get("/status", params={"limit": 100})).json()["recent"]
    assert len(all_events) == 6

    matched = (await file_client.get("/status", params={"q": "事件"})).json()["recent"]
    assert len(matched) == 3 and all("事件" in e["title"] for e in matched)

    by_source = (await file_client.get("/status", params={"source": "test"})).json()["recent"]
    assert len(by_source) == 6
    assert (await file_client.get("/status", params={"source": "nobody"})).json()["recent"] == []

    routed = (await file_client.get("/status", params={"outcome": "routed"})).json()["recent"]
    assert len(routed) == 6

    # Pagination walks backwards by id without gaps or repeats.
    page1 = (await file_client.get("/status", params={"limit": 2})).json()["recent"]
    page2 = (await file_client.get("/status", params={"limit": 2, "before_id": page1[-1]["id"]})).json()["recent"]
    assert [e["id"] for e in page1] + [e["id"] for e in page2] == sorted([e["id"] for e in all_events], reverse=True)[
        :4
    ]


async def test_search_limit_is_clamped(file_client):
    await file_client.post("/hook/test", json={"title": "one"})
    # A hostile limit must not turn the ledger view into a full-table dump.
    response = await file_client.get("/status", params={"limit": 99999})
    assert response.status_code == 200 and len(response.json()["recent"]) == 1
