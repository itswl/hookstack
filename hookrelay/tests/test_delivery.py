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
        return True, "http 200", b"{}"

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
        return False, "http 500: nope", b"{}"

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


async def test_ledger_keeps_the_bytes_that_left(store, cfg, settings, monkeypatch):
    """Every delivery row keeps the exact body of its last attempt — the
    receiver-dispute answer lives in the ledger, not in a re-derivation."""
    result = await _route_one(store, cfg)
    import hookrelay.delivery as delivery_mod

    async def ok_send(client, channel, message):
        return True, "http 200", f'{{"to":"{channel.name}"}}'.encode()

    monkeypatch.setattr(delivery_mod.channels, "send", ok_send)
    await process_due(store, cfg, settings, FakeClient(), now=1001.0)

    trip = await store.round_trip(result["event_id"])
    deliveries = trip["origin"]["deliveries"]
    assert {d["channel"] for d in deliveries} == {"feishu-main", "ding-main", "mirror"}
    bodies = {d["channel"]: d["sent_body"] for d in deliveries}
    assert bodies["feishu-main"] == '{"to":"feishu-main"}'
    assert all(d["status"] == "sent" for d in deliveries)


async def test_send_returns_the_exact_bytes_posted(cfg):
    """One serialization for the wire and the ledger — what send() reports as
    the body is byte-identical to what the client was given."""
    from hookrelay import channels

    captured: dict = {}

    class Response:
        status_code = 200
        text = ""

        def json(self):
            raise ValueError("not json")

    class Client:
        async def post(self, url, content=None, headers=None):
            captured["content"] = content
            return Response()

    message = {
        "event_id": 7,
        "source": "grafana",
        "title": "db down",
        "body": "x",
        "level": "high",
        "fields": {},
        "received_at": 0.0,
        "payload": {"raw": True},
    }
    ok, detail, body = await channels.send(Client(), cfg.channels["mirror"], message)
    assert ok, detail
    assert isinstance(captured["content"], bytes)
    assert body == captured["content"]


async def test_rate_limit_defers_without_burning_attempts(store, cfg, settings, monkeypatch):
    # Two events → two mirror deliveries; mirror allows 1/minute.
    await _route_one(store, cfg, now=1000.0)
    second = await handle_hook(store, cfg, cfg.sources["grafana"], dict(PAYLOAD, title="cache down"), now=1000.0)
    assert second["outcome"] == "routed"

    import hookrelay.delivery as delivery_mod

    async def ok_send(client, channel, message):
        return True, "http 200", b"{}"

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
        return True, "http 200", b"{}"

    original = delivery_mod.channels.send
    delivery_mod.channels.send = ok_send
    try:
        await process_due(store, slim, settings, FakeClient(), now=1001.0)
    finally:
        delivery_mod.channels.send = original

    cursor = await store.db.execute("SELECT status, last_error FROM deliveries WHERE channel = 'mirror'")
    row = await cursor.fetchone()
    assert row["status"] == "dead" and "no longer configured" in row["last_error"]


async def test_recovery_flag_survives_the_ledger_round_trip(store):
    """extract -> insert -> due_deliveries must carry the stated flag: the
    delivery loop rebuilds the message from ledger COLUMNS, so a flag that
    only lived in the extracted dict evaporated between the door and the
    channel (found live: the judge saw is_recovery on zero shadow events).
    Tri-state: an event whose template stated nothing must arrive WITHOUT
    the key, so receivers keep their own detection."""
    import time as _time

    now = _time.time()
    stated = await store.insert_event(
        source="ww", fp="fp-r1",
        extracted={"title": "t", "body": "b", "level": "low", "fields": {}, "is_recovery": True},
        payload_json="{}", now=now)
    silent = await store.insert_event(
        source="ww", fp="fp-r2",
        extracted={"title": "t2", "body": "b", "level": "high", "fields": {}},
        payload_json="{}", now=now)
    await store.enqueue_delivery(stated, "to-judge", now)
    await store.enqueue_delivery(silent, "to-judge", now)
    rows = {row["event_id"]: row for row in await store.due_deliveries(now + 1)}
    assert rows[stated]["is_recovery"] == 1
    assert rows[silent]["is_recovery"] is None
