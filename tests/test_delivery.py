"""Delivery semantics: backoff, dead letters, and rate limits that defer."""

from __future__ import annotations

import hookrelay.channels as channels_mod
from hookrelay.delivery import backoff_delay, process_due
from hookrelay.pipeline import handle_hook

PAYLOAD = {"title": "db down", "message": "x", "state": "alerting"}


class FakeClient:
    """Unused by tests that monkeypatch channels.send; required by signature."""


async def _route_one(store, cfg, now=1000.0):
    result = await handle_hook(store, cfg, cfg.sources["grafana"], PAYLOAD, now=now)
    assert result["outcome"] == "routed"
    return result


async def test_success_marks_sent(store, cfg, settings, monkeypatch):
    await _route_one(store, cfg)
    sent = []

    async def fake_send(client, channel, message):
        sent.append((channel.name, message["title"]))
        return True, "http 200"

    monkeypatch.setattr(channels_mod, "send", fake_send)
    import hookrelay.delivery as delivery_mod

    monkeypatch.setattr(delivery_mod.channels, "send", fake_send)
    processed = await process_due(store, cfg, settings, FakeClient(), now=1001.0)

    assert processed == 3
    assert sorted(name for name, _ in sent) == ["ding-main", "feishu-main", "mirror"]
    assert (await store.queue_counts()) == {"queued": 0, "sent": 3, "dead": 0}


async def test_failure_backs_off_then_dies_at_max_attempts(store, cfg, settings, monkeypatch):
    await _route_one(store, cfg)
    import hookrelay.delivery as delivery_mod

    async def always_fail(client, channel, message):
        return False, "http 500: nope"

    monkeypatch.setattr(delivery_mod.channels, "send", always_fail)

    now = 1001.0
    # settings.max_attempts is 3: attempt→retry(+30s), attempt→retry(+60s), attempt→dead.
    await process_due(store, cfg, settings, FakeClient(), now=now)
    counts = await store.queue_counts()
    assert counts["queued"] == 3 and counts["dead"] == 0

    # Not due yet — backoff is real, nothing processes early.
    assert await process_due(store, cfg, settings, FakeClient(), now=now + 1) == 0

    await process_due(store, cfg, settings, FakeClient(), now=now + backoff_delay(1) + 1)
    await process_due(store, cfg, settings, FakeClient(), now=now + backoff_delay(1) + backoff_delay(2) + 2)
    counts = await store.queue_counts()
    assert counts["dead"] == 3 and counts["queued"] == 0

    # The dead letter keeps its last error in the open.
    recent = await store.recent_events(5)
    assert recent[0]["deliveries"][0]["last_error"].startswith("http 500")


async def test_rate_limit_defers_without_burning_attempts(store, cfg, settings, monkeypatch):
    # Two events → two mirror deliveries; mirror allows 1/minute.
    await _route_one(store, cfg, now=1000.0)
    second = await handle_hook(store, cfg, cfg.sources["grafana"], dict(PAYLOAD, title="cache down"), now=1000.0)
    assert second["outcome"] == "routed"

    import hookrelay.delivery as delivery_mod

    async def ok_send(client, channel, message):
        return True, "http 200"

    monkeypatch.setattr(delivery_mod.channels, "send", ok_send)
    await process_due(store, cfg, settings, FakeClient(), now=1001.0)

    cursor = await store.db.execute("SELECT status, attempts FROM deliveries WHERE channel = 'mirror' ORDER BY id")
    rows = [dict(r) for r in await cursor.fetchall()]
    assert [r["status"] for r in rows] == ["sent", "queued"]
    # The deferred one burned NO attempt — it was pushback, not failure.
    assert rows[1]["attempts"] == 0

    # A minute later the limiter window has rolled; it goes out.
    await process_due(store, cfg, settings, FakeClient(), now=1062.0)
    cursor = await store.db.execute("SELECT status FROM deliveries WHERE channel = 'mirror' ORDER BY id")
    assert [r["status"] for r in await cursor.fetchall()] == ["sent", "sent"]
    assert (await store.queue_counts())["queued"] == 0


async def test_unconfigured_channel_dead_letters_with_reason(store, cfg, settings):
    await _route_one(store, cfg)
    slim = cfg.__class__(
        sources=cfg.sources, channels={k: v for k, v in cfg.channels.items() if k != "mirror"}, routes=cfg.routes
    )
    # send never fires for the missing channel; others untouched (not due filter here—only mirror missing)
    import hookrelay.delivery as delivery_mod

    async def ok_send(client, channel, message):
        return True, "http 200"

    original = delivery_mod.channels.send
    delivery_mod.channels.send = ok_send
    try:
        await process_due(store, slim, settings, FakeClient(), now=1001.0)
    finally:
        delivery_mod.channels.send = original

    cursor = await store.db.execute("SELECT status, last_error FROM deliveries WHERE channel = 'mirror'")
    row = await cursor.fetchone()
    assert row["status"] == "dead" and "no longer configured" in row["last_error"]
