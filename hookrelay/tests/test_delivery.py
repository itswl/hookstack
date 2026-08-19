"""Delivery semantics: backoff, dead letters, and rate limits that defer."""

from __future__ import annotations

import json

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


async def test_the_ledger_keeps_the_alert_but_not_what_signs_it(cfg):
    """The schema promises "body only, never the headers: headers carry
    signatures and tokens" — true for every dialect except Feishu's custom bot,
    which signs in the BODY. So a live `sign` was stored and /trace served it
    under a read guard that is open until a token is configured: anyone who
    could reach the board could lift it and post into the group. The alert text
    is what answers a receiver's dispute; the signature is derived and is
    nobody's evidence."""
    from hookrelay import channels

    signed = cfg.channels["feishu-main"]
    message = {
        "event_id": 7,
        "source": "grafana",
        "title": "db down",
        "body": "x",
        "level": "high",
        "fields": {},
        "received_at": 0.0,
        "payload": {},
    }
    _url, payload, _headers = channels.build_feishu(signed, message, 1000.0)
    payload.update(channels._feishu_sign_fields("bot-secret", 1000.0))
    wire = json.dumps(payload, ensure_ascii=False).encode()

    # The bytes that leave the socket carry the signature; the ledger copy does not.
    assert b"sign" in wire
    stored = channels.redact_for_ledger(wire)
    assert stored is not None
    assert json.loads(stored)["sign"] == "[redacted]"
    assert "db down" in stored, "the alert content still answers a dispute"

    # A body with nothing to hide is passed through untouched.
    assert channels.redact_for_ledger(b'{"to":"mirror"}') == '{"to":"mirror"}'
    assert channels.redact_for_ledger(b"not json") == "not json"
    assert channels.redact_for_ledger(None) is None


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


async def test_one_channels_failure_does_not_abandon_its_siblings(store, cfg, settings, monkeypatch):
    """The fan-out gathered its channel groups without return_exceptions, so a
    store error in one group propagated out of process_due while the sibling
    groups kept running ORPHANED — and the next tick re-picked the rows those
    orphans were half-way through (due_deliveries selects on
    status/next_attempt_at; it claims nothing), sending a second copy to a
    downstream that already had the alert.

    So: the failure is contained and named, every other group is FINISHED before
    process_due returns, and the failed row is left queued for the next tick."""
    import asyncio

    import hookrelay.delivery as delivery_mod

    await _route_one(store, cfg)
    sent: list[str] = []

    async def slow_send(client, channel, message):
        # A real send yields to the loop; an abandoned group is one that has not
        # come back yet when process_due returns.
        await asyncio.sleep(0.01)
        sent.append(channel.name)
        return True, "http 200", b"{}"

    monkeypatch.setattr(delivery_mod.channels, "send", slow_send)

    doomed = next(row["id"] for row in await store.due_deliveries(now=1001.0) if row["channel"] == "mirror")
    original_mark_sent = store.mark_sent

    async def flaky_mark_sent(delivery_id, now, sent_body=None):
        if delivery_id == doomed:
            raise RuntimeError("database is locked")
        await original_mark_sent(delivery_id, now, sent_body)

    monkeypatch.setattr(store, "mark_sent", flaky_mark_sent)

    processed = await process_due(store, cfg, settings, FakeClient(), now=1001.0)

    assert processed == 2, "the two healthy groups are accounted for; the broken one is not"
    assert sorted(sent) == ["ding-main", "feishu-main", "mirror"]
    counts = await store.queue_counts()
    assert counts["sent"] == 2, "no sibling was left mid-flight when process_due returned"
    assert counts["queued"] == 1 and counts["dead"] == 0, "the broken channel's row waits for the next tick"


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
        source="ww",
        fp="fp-r1",
        extracted={"title": "t", "body": "b", "level": "low", "fields": {}, "is_recovery": True},
        payload_json="{}",
        now=now,
    )
    silent = await store.insert_event(
        source="ww",
        fp="fp-r2",
        extracted={"title": "t2", "body": "b", "level": "high", "fields": {}},
        payload_json="{}",
        now=now,
    )
    await store.enqueue_delivery(stated, "to-judge", now)
    await store.enqueue_delivery(silent, "to-judge", now)
    rows = {row["event_id"]: row for row in await store.due_deliveries(now + 1)}
    assert rows[stated]["is_recovery"] == 1
    assert rows[silent]["is_recovery"] is None
