"""Metrics exposition and the self-alarm — who watches the watchman.

Every series asserted here has a consumer in the estate (Prometheus scrapes,
Grafana draws, Alertmanager pages on dead letters). Instruments without a
consumer do not belong, so this file doubles as the consumer list.
"""

from __future__ import annotations

import httpx
import pytest

from hookrelay import metrics
from hookrelay.alarm import SelfAlarm


def test_render_emits_help_type_and_all_consumer_series() -> None:
    metrics._events.clear()
    metrics._deliveries.clear()
    metrics.record_event("grafana", "routed")
    metrics.record_event("grafana", "duplicate")
    metrics.record_event("grafana", "routed")
    metrics.record_delivery("feishu-ops", "sent")
    metrics.record_delivery("feishu-ops", "dead")

    text = metrics.render(
        queue={"queued": 2, "sent": 9, "dead": 1},
        fuse={"loud": {"suppressed": 7, "rejected": 2}},
        silences=1,
        retention_days=14,
    )

    assert 'hookrelay_events_total{source="grafana",outcome="routed"} 2' in text
    assert 'hookrelay_events_total{source="grafana",outcome="duplicate"} 1' in text
    assert 'hookrelay_deliveries_total{channel="feishu-ops",result="dead"} 1' in text
    assert 'hookrelay_outbox{status="dead"} 1' in text
    assert 'hookrelay_fuse_total{source="loud",stage="soft"} 7' in text
    assert 'hookrelay_fuse_total{source="loud",stage="hard"} 2' in text
    assert "hookrelay_silences_active 1" in text
    assert "hookrelay_up 1" in text
    # Exposition hygiene: every metric carries HELP and TYPE, and the body ends
    # with a newline (scrapers reject a truncated final line).
    for name in ("hookrelay_events_total", "hookrelay_outbox", "hookrelay_fuse_total"):
        assert f"# HELP {name} " in text and f"# TYPE {name} " in text
    assert text.endswith("\n")


def test_label_values_are_escaped() -> None:
    metrics._events.clear()
    metrics.record_event('weird"door\n', "routed")
    text = metrics.render(queue={}, fuse={}, silences=0, retention_days=0)
    assert 'source="weird\\"door "' in text, "quotes escaped, newline flattened"


class _RecordingClient:
    def __init__(self, fail: bool = False) -> None:
        self.posts: list[dict] = []
        self.fail = fail

    async def post(self, url: str, *, json: dict, timeout: float) -> object:
        self.posts.append({"url": url, "json": json})
        if self.fail:
            raise httpx.ConnectError("alarm channel down")
        return object()


@pytest.mark.asyncio
async def test_alarm_sends_once_then_throttles_and_folds_the_count() -> None:
    alarm = SelfAlarm("https://bot.example/hook", min_interval_seconds=600)
    client = _RecordingClient()

    await alarm.dead_letter(client, channel="feishu-ops", event_id=7, error="http 500", now=1000.0)
    assert len(client.posts) == 1
    text = client.posts[0]["json"]["content"]["text"]
    assert "feishu-ops" in text and "#7" in text and "http 500" in text

    # Inside the window: suppressed, not sent.
    for _ in range(4):
        await alarm.dead_letter(client, channel="feishu-ops", event_id=8, error="http 500", now=1100.0)
    assert len(client.posts) == 1

    # After the window: one message, carrying the suppressed tally so
    # throttling never hides the scale.
    await alarm.dead_letter(client, channel="feishu-ops", event_id=9, error="http 500", now=1700.0)
    assert len(client.posts) == 2
    assert "另有 4 条" in client.posts[1]["json"]["content"]["text"]


@pytest.mark.asyncio
async def test_alarm_failure_never_raises_and_permits_a_retry() -> None:
    alarm = SelfAlarm("https://bot.example/hook", min_interval_seconds=600)
    client = _RecordingClient(fail=True)

    await alarm.dead_letter(client, channel="c", event_id=1, error="e", now=1000.0)  # must not raise
    # A failed alarm does not consume the window — the next dead letter tries again.
    await alarm.dead_letter(client, channel="c", event_id=2, error="e", now=1001.0)
    assert len(client.posts) == 2


@pytest.mark.asyncio
async def test_unconfigured_alarm_is_inert() -> None:
    alarm = SelfAlarm("", min_interval_seconds=600)
    client = _RecordingClient()
    assert alarm.enabled is False
    await alarm.dead_letter(client, channel="c", event_id=1, error="e", now=1000.0)
    assert client.posts == []


async def test_metrics_endpoint_is_read_token_guarded(client) -> None:
    assert (await client.get("/metrics")).status_code == 401
    response = await client.get("/metrics", headers={"X-Read-Token": "read-t"})
    assert response.status_code == 200
    assert "hookrelay_up 1" in response.text
    assert response.headers["content-type"].startswith("text/plain")


async def test_dead_letters_alarm_through_the_worker(store, cfg, settings, monkeypatch) -> None:
    """The integration that matters: a delivery exhausting its attempts must
    reach the alarm, and a merely-failed one must not."""
    import hookrelay.delivery as delivery_mod
    from hookrelay.delivery import process_due
    from hookrelay.pipeline import handle_hook

    await handle_hook(
        store, cfg, cfg.sources["grafana"], {"title": "t", "message": "m", "state": "alerting"}, now=1000.0
    )

    async def always_fail(client, channel, message):
        return False, "http 500: nope"

    monkeypatch.setattr(delivery_mod.channels, "send", always_fail)
    alarm = SelfAlarm("https://bot.example/hook", min_interval_seconds=0)
    client = _RecordingClient()

    # settings.max_attempts is 3: two rounds fail-and-retry, the third dies.
    await process_due(store, cfg, settings, client, now=1001.0, alarm=alarm)
    assert client.posts == [], "a retryable failure is not an alarm"

    await process_due(store, cfg, settings, client, now=1001.0 + 31, alarm=alarm)
    await process_due(store, cfg, settings, client, now=1001.0 + 92, alarm=alarm)
    assert (await store.queue_counts())["dead"] == 3
    assert len(client.posts) >= 1, "dead letters must announce themselves"


# ── delivery robustness: breaker, parallelism, idempotency ───────────────────


def test_breaker_opens_after_threshold_then_probes_once() -> None:
    from hookrelay.breaker import CircuitBreaker

    breaker = CircuitBreaker(threshold=3, cooldown_seconds=60)

    for _ in range(2):
        assert breaker.allows("feishu", 1000.0)
        breaker.record_failure("feishu", 1000.0)
    assert breaker.allows("feishu", 1000.0), "still closed below the threshold"
    breaker.record_failure("feishu", 1000.0)

    # Open: deliveries wait instead of burning attempts against a wall.
    assert not breaker.allows("feishu", 1010.0)
    assert breaker.is_open("feishu", 1010.0)
    assert breaker.snapshot(1010.0) == {"feishu": "open"}

    # Half-open after cooldown: exactly ONE probe, not a stampede.
    assert breaker.allows("feishu", 1061.0)
    assert not breaker.allows("feishu", 1061.0), "a queue of 500 must not become 500 probes"
    assert breaker.snapshot(1061.0) == {"feishu": "half-open"}

    # A successful probe closes it completely.
    breaker.record_success("feishu")
    assert breaker.allows("feishu", 1062.0)
    assert breaker.snapshot(1062.0) == {}


def test_breaker_is_per_channel() -> None:
    from hookrelay.breaker import CircuitBreaker

    breaker = CircuitBreaker(threshold=1, cooldown_seconds=60)
    breaker.record_failure("sick", 1000.0)
    assert not breaker.allows("sick", 1001.0)
    assert breaker.allows("healthy", 1001.0), "one sick channel must not gate the others"


async def test_open_breaker_defers_without_burning_attempts(store, cfg, settings, monkeypatch) -> None:
    import hookrelay.delivery as delivery_mod
    from hookrelay.breaker import CircuitBreaker
    from hookrelay.delivery import process_due
    from hookrelay.pipeline import handle_hook

    await handle_hook(
        store, cfg, cfg.sources["grafana"], {"title": "t", "message": "m", "state": "alerting"}, now=1000.0
    )

    async def always_fail(client, channel, message):
        return False, "connection refused"

    monkeypatch.setattr(delivery_mod.channels, "send", always_fail)
    breaker = CircuitBreaker(threshold=1, cooldown_seconds=600)

    await process_due(store, cfg, settings, object(), now=1001.0, breaker=breaker)
    cursor = await store.db.execute("SELECT status, attempts FROM deliveries ORDER BY id")
    after_first = [dict(r) for r in await cursor.fetchall()]
    burned = sum(r["attempts"] for r in after_first)

    # Everything is due again, but the breakers are open: rows defer.
    await process_due(store, cfg, settings, object(), now=1002.0, breaker=breaker)
    cursor = await store.db.execute("SELECT status, attempts FROM deliveries ORDER BY id")
    after_second = [dict(r) for r in await cursor.fetchall()]
    assert sum(r["attempts"] for r in after_second) == burned, "an open breaker burns no attempt"
    assert all(r["status"] == "queued" for r in after_second)


async def test_channels_drain_in_parallel_not_head_of_line(store, cfg, settings, monkeypatch) -> None:
    """A channel that hangs must not block deliveries to healthy channels."""
    import asyncio

    import hookrelay.delivery as delivery_mod
    from hookrelay.delivery import process_due
    from hookrelay.pipeline import handle_hook

    await handle_hook(
        store, cfg, cfg.sources["grafana"], {"title": "t", "message": "m", "state": "alerting"}, now=1000.0
    )
    order: list[str] = []

    async def slow_feishu(client, channel, message):
        if channel.name == "feishu-main":
            await asyncio.sleep(0.05)  # the hanging peer
        order.append(channel.name)
        return True, "http 200"

    monkeypatch.setattr(delivery_mod.channels, "send", slow_feishu)
    await process_due(store, cfg, settings, object(), now=1001.0)

    assert len(order) == 3
    assert order[-1] == "feishu-main", "the slow channel finished last, the others did not wait for it"


async def test_idempotency_key_travels_as_a_header_not_in_the_signed_body(store, cfg, settings, monkeypatch) -> None:
    import hookrelay.delivery as delivery_mod
    from hookrelay.channels import send as real_send
    from hookrelay.delivery import process_due
    from hookrelay.pipeline import handle_hook

    captured: list[dict] = []

    class _Client:
        async def post(self, url, *, content=None, json=None, headers=None):
            captured.append({"headers": headers or {}, "content": content, "json": json})

            class _R:
                status_code = 200
                text = "{}"

                def json(self):
                    return {}

            return _R()

    monkeypatch.setattr(delivery_mod.channels, "send", real_send)
    await handle_hook(
        store, cfg, cfg.sources["grafana"], {"title": "t", "message": "m", "state": "alerting"}, now=1000.0
    )
    await process_due(store, cfg, settings, _Client(), now=1001.0)

    generic = [c for c in captured if c["content"] is not None]
    assert generic, "the generic channel sends bytes"
    assert generic[0]["headers"]["X-Hook-Idempotency-Key"], "receivers need a dedupe key"
    body = generic[0]["content"].decode()
    assert "_idempotency_key" not in body, "transport keys must never enter the signed content"
    assert '"payload"' not in body, "nor the raw payload in normalized mode"
