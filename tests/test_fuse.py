"""The storm fuse: volume protection at the door, in two stages.

Why this exists at all: the reference production deploy has a brain with its
own ingress backpressure behind the relay, but a relay in front of something
WITHOUT backpressure (WebhookWise-lite) must carry its own fuse. Content dedup
cannot do this job — a high-cardinality flood has no repeated payload.
"""

from __future__ import annotations

import json

import httpx
import pytest

from hookrelay.app import create_app
from hookrelay.fuse import StormFuse
from hookrelay.settings import Settings

FUSED_YAML = """
sources:
  - name: loud
    secret: ""
    title: "{n}"
    storm_threshold: 3
    storm_window_seconds: 60
  - name: unfused
    secret: ""
    title: "{n}"
channels:
  - name: sink
    type: generic
    url: https://sink.example/in
routes:
  - name: all
    source: "*"
    send_to: [sink]
"""


def test_soft_stage_starts_above_threshold() -> None:
    fuse = StormFuse()
    verdicts = [fuse.check("s", threshold=3, window_seconds=60, now=1000.0) for _ in range(5)]
    assert verdicts == ["pass", "pass", "pass", "suppress", "suppress"]


def test_hard_stage_at_ten_times_threshold() -> None:
    fuse = StormFuse()
    verdicts = [fuse.check("s", 2, 60, 1000.0) for _ in range(22)]
    assert verdicts[:2] == ["pass", "pass"]
    assert verdicts[2:20] == ["suppress"] * 18
    assert verdicts[20:] == ["reject", "reject"], "beyond 10x the ledger itself needs protecting"


def test_window_slides_so_a_calm_source_recovers() -> None:
    fuse = StormFuse()
    for _ in range(4):
        fuse.check("s", 3, 60, 1000.0)
    assert fuse.check("s", 3, 60, 1000.0) == "suppress"
    # A minute later the window has rolled; the door opens again.
    assert fuse.check("s", 3, 60, 1061.0) == "pass"


def test_threshold_zero_means_no_fuse() -> None:
    fuse = StormFuse()
    assert [fuse.check("s", 0, 60, 1000.0) for _ in range(50)] == ["pass"] * 50


def test_counters_are_per_source_and_quiet_when_healthy() -> None:
    fuse = StormFuse()
    for _ in range(5):
        fuse.check("loud", 3, 60, 1000.0)
    for _ in range(2):
        fuse.check("quiet", 3, 60, 1000.0)
    snapshot = fuse.snapshot()
    assert snapshot == {"loud": {"suppressed": 2, "rejected": 0}}
    assert "quiet" not in snapshot, "a healthy source contributes no noise to the board"


@pytest.fixture
async def fused_client(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(FUSED_YAML, encoding="utf-8")
    settings = Settings(
        config_path=str(config_path),
        db_path=str(tmp_path / "t.db"),
        plugins_dir=str(tmp_path / "none"),
        admin_token="admin-t",
        read_token="",
        max_body_bytes=256 * 1024,
        max_attempts=3,
        retention_days=14,
        worker_interval_seconds=0.01,
    )
    app = create_app(settings=settings)
    async with (
        httpx.ASGITransport(app=app) as transport,
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://t") as client,
    ):
        yield client


async def test_suppressed_events_keep_their_account(fused_client):
    """The storm is exactly when you most need to know what arrived: a fused
    event is recorded with a named code and its window count, walks no
    pipeline, and reaches no channel."""
    outcomes = []
    for n in range(5):
        response = await fused_client.post("/hook/loud", json={"n": n})
        outcomes.append((response.status_code, response.json().get("skip_code")))

    assert outcomes[:3] == [(200, None)] * 3
    assert outcomes[3:] == [(200, "storm_suppressed")] * 2

    status = (await fused_client.get("/status")).json()
    assert status["fuse"] == {"loud": {"suppressed": 2, "rejected": 0}}
    # Three routed events → three deliveries; the two fused ones enqueued none.
    assert status["queue"]["queued"] + status["queue"]["sent"] + status["queue"]["dead"] == 3

    fused = [e for e in status["recent"] if e["skip_code"] == "storm_suppressed"]
    assert len(fused) == 2
    step = fused[0]["steps"][0]
    assert step["gate"] == "storm_fuse" and step["threshold"] == 3 and step["window_count"] >= 4


async def test_hard_stage_returns_429_without_storing(fused_client):
    for n in range(31):
        response = await fused_client.post("/hook/loud", json={"n": n})
    assert response.status_code == 429

    status = (await fused_client.get("/status")).json()
    assert status["fuse"]["loud"]["rejected"] >= 1
    # 3 routed + 27 suppressed rows; rejections never touch storage.
    assert len(status["recent"]) == 30


async def test_unfused_source_is_untouched(fused_client):
    for n in range(8):
        response = await fused_client.post("/hook/unfused", json={"n": n})
        assert response.json()["outcome"] == "routed"
    status = (await fused_client.get("/status")).json()
    assert status["fuse"] == {}, "no fuse configured, no fuse behaviour"


async def test_signature_check_still_precedes_the_fuse(tmp_path):
    """An unsigned flood must be rejected as unauthenticated (free), never
    consume fuse budget or appear in the tally."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        FUSED_YAML.replace('  - name: loud\n    secret: ""', '  - name: loud\n    secret: "s3"'),
        encoding="utf-8",
    )
    settings = Settings(
        config_path=str(config_path),
        db_path=str(tmp_path / "t2.db"),
        plugins_dir=str(tmp_path / "none"),
        admin_token="admin-t",
        read_token="",
        max_body_bytes=256 * 1024,
        max_attempts=3,
        retention_days=14,
        worker_interval_seconds=0.01,
    )
    app = create_app(settings=settings)
    async with (
        httpx.ASGITransport(app=app) as transport,
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://t") as client,
    ):
        for n in range(10):
            response = await client.post("/hook/loud", content=json.dumps({"n": n}).encode())
            assert response.status_code == 401
        assert (await client.get("/status")).json()["fuse"] == {}
