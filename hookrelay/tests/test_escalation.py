"""Nobody was awake: a delivered alert that no human touched, sent on.

The family judges an alert well, dresses it well and delivers it well — and had
no answer for the case where it lands in a channel at 3am and nobody acts. This
is the smallest answer that needs no identity model: the card_actions ledger is
the only evidence the pipe has that a person was there, so "untouched" is a
question it can already ask.

Off unless configured. An escalation nobody asked for is a second alert about
the first alert, in the middle of the night.
"""

from __future__ import annotations

import dataclasses
import time

import httpx
import pytest

from hookrelay.app import create_app
from hookrelay.config import Config, ConfigError
from hookrelay.delivery import process_due
from hookrelay.pipeline import handle_hook

RAW = {"title": "Payment gateway 5xx", "message": "gateway-2 failing", "state": "alerting"}


def _cfg(escalation: dict | None = None) -> Config:
    body = {
        "sources": [
            {
                "name": "grafana",
                "secret": "",
                "title": "{title}",
                "body": "{message}",
                "level": "{state}",
                "level_map": {"alerting": "critical"},
            }
        ],
        "channels": [
            {"name": "ops-feishu", "type": "feishu", "url": "https://feishu.example/hook"},
            {"name": "pager", "type": "generic", "url": "https://pager.example/in"},
        ],
        "routes": [{"name": "all", "source": "*", "send_to": ["ops-feishu"]}],
        "card_actions": {"silence": {}},
    }
    if escalation is not None:
        body["escalation"] = escalation
    return Config.from_dict(body)


async def _deliver_one(store, cfg, settings, monkeypatch, now: float) -> int:
    """One alert, routed and actually delivered — the precondition."""
    import hookrelay.delivery as delivery_mod

    async def ok_send(client, channel, message):
        return True, "http 200", b"{}"

    monkeypatch.setattr(delivery_mod.channels, "send", ok_send)
    result = await handle_hook(store, cfg, cfg.sources["grafana"], RAW, now=now)
    await process_due(store, cfg, settings, object(), now=now)
    return int(result["event_id"])


async def _channels_for(store, event_id: int) -> list[str]:
    cursor = await store.db.execute("SELECT channel FROM deliveries WHERE event_id = ? ORDER BY id", (event_id,))
    return [row["channel"] for row in await cursor.fetchall()]


# ── the config gate ──────────────────────────────────────────────────────────


def test_escalation_is_off_until_asked_for() -> None:
    assert _cfg().escalation is None


def test_an_escalation_that_could_never_arrive_fails_at_boot() -> None:
    with pytest.raises(ConfigError, match="at least 1"):
        _cfg({"after_minutes": 0, "send_to": ["pager"]})
    with pytest.raises(ConfigError, match="names no channel"):
        _cfg({"after_minutes": 15, "send_to": []})
    with pytest.raises(ConfigError, match="unconfigured channel"):
        _cfg({"after_minutes": 15, "send_to": ["nowhere"]})


# ── the sweep ────────────────────────────────────────────────────────────────


async def test_an_untouched_alert_is_sent_on_once(store, settings, monkeypatch) -> None:
    """The whole point: delivered, ignored, escalated — and escalated ONCE, not
    once per worker tick."""
    cfg = _cfg({"after_minutes": 15, "send_to": ["pager"], "levels": ["critical"]})
    event_id = await _deliver_one(store, cfg, settings, monkeypatch, now=1000.0)
    assert await _channels_for(store, event_id) == ["ops-feishu"]

    cold = await store.cold_events(before=1000.0, levels=("critical",))
    assert [row["id"] for row in cold] == [event_id]

    assert await store.mark_escalated(event_id, 2000.0) is True
    assert await store.mark_escalated(event_id, 2001.0) is False, "one escalation per event, not per tick"
    assert await store.cold_events(before=1000.0, levels=("critical",)) == []


async def test_a_pressed_button_is_a_human_being_awake(store, settings, monkeypatch) -> None:
    """A silence, a follow-up, a "not worth it" — any press means somebody was
    there, and an alert somebody handled must never be escalated."""
    cfg = _cfg({"after_minutes": 15, "send_to": ["pager"]})
    event_id = await _deliver_one(store, cfg, settings, monkeypatch, now=1000.0)

    await store.spend_action("jti-1", kind="silence", event_id=event_id, correlation_id="", actor="ou_a", now=1100.0)
    assert await store.cold_events(before=1000.0, levels=()) == []


async def test_a_press_recorded_against_the_verdict_still_counts(store, settings, monkeypatch) -> None:
    """The button lives on the VERDICT's card, so its press carries the
    correlation id (`hr-<event>`) rather than the front-door event's own id. If
    that did not count, every alert an operator actually handled would escalate."""
    cfg = _cfg({"after_minutes": 15, "send_to": ["pager"]})
    event_id = await _deliver_one(store, cfg, settings, monkeypatch, now=1000.0)

    await store.spend_action(
        "jti-2", kind="useful", event_id=None, correlation_id=f"hr-{event_id}", actor="ou_b", now=1100.0
    )
    assert await store.cold_events(before=1000.0, levels=()) == []


async def test_an_undelivered_alert_is_not_unacknowledged(store, settings, monkeypatch) -> None:
    """Nothing reached a human, so nothing was ignored — and the dead-letter
    alarm already owns that story. Escalating it would be a second alarm for one
    failure."""
    cfg = _cfg({"after_minutes": 15, "send_to": ["pager"]})
    await handle_hook(store, cfg, cfg.sources["grafana"], RAW, now=1000.0)  # routed, never delivered
    assert await store.cold_events(before=1000.0, levels=()) == []


async def test_the_level_filter_decides_what_deserves_a_second_delivery(store, settings, monkeypatch) -> None:
    cfg = _cfg({"after_minutes": 15, "send_to": ["pager"], "levels": ["high"]})
    await _deliver_one(store, cfg, settings, monkeypatch, now=1000.0)  # arrives critical
    assert await store.cold_events(before=1000.0, levels=("high",)) == []
    assert await store.cold_events(before=1000.0, levels=("critical",)) != []


async def test_a_fresh_alert_is_left_alone(store, settings, monkeypatch) -> None:
    """Somebody may simply not have looked yet."""
    cfg = _cfg({"after_minutes": 15, "send_to": ["pager"]})
    await _deliver_one(store, cfg, settings, monkeypatch, now=1000.0)
    assert await store.cold_events(before=1000.0 - 15 * 60, levels=()) == []


# ── end to end, through the worker ───────────────────────────────────────────


async def test_the_worker_escalates_and_the_delivery_is_an_ordinary_one(settings, tmp_path, monkeypatch) -> None:
    """Enqueued against the SAME event, so it inherits the retry, the rate limit
    and the ledger row — and the board reads it as what it is: this alert, sent
    somewhere else, later. A second EVENT would make the ledger lie about how
    many alerts arrived."""
    import hookrelay.delivery as delivery_mod

    sent: list[str] = []

    async def ok_send(client, channel, message):
        sent.append(channel.name)
        return True, "http 200", b"{}"

    monkeypatch.setattr(delivery_mod.channels, "send", ok_send)
    cfg = _cfg({"after_minutes": 1, "send_to": ["pager"], "levels": ["critical"]})
    # action_secret is not decoration here: without it no card carries a button,
    # so no press is possible and the sweep disarms itself on purpose. A test
    # that skipped it would have been asserting against a disabled feature.
    wired = dataclasses.replace(
        settings,
        db_path=str(tmp_path / "esc.db"),
        worker_interval_seconds=0.01,
        action_secret="card-s3cret",
    )
    app = create_app(settings=wired, cfg=cfg)

    async with (
        httpx.ASGITransport(app=app) as transport,
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://t") as client,
    ):
        posted = await client.post("/hook/grafana", json=RAW)
        event_id = posted.json()["event_id"]
        # The worker runs on the real clock, so age the row rather than waiting.
        await app.state.store.db.execute(
            "UPDATE events SET received_at = ? WHERE id = ?", (time.time() - 3600, event_id)
        )
        await app.state.store.db.commit()

        for _ in range(200):
            if "pager" in sent:
                break
            await __import__("asyncio").sleep(0.02)

        assert "pager" in sent, f"the cold alert was never escalated (sent: {sent})"
        assert await _channels_for(app.state.store, int(event_id)) == ["ops-feishu", "pager"]


# ── it cannot fire where nobody can press anything ──────────────────────────


def test_escalation_disarms_itself_when_no_press_is_possible() -> None:
    """The sweep asks "did any human touch this?" and a card action press is the
    only evidence there is. On a deployment where no press can ever happen it
    would read every alert as ignored — turning a feature meant to catch the one
    alert nobody saw into a second copy of every alert."""
    from hookrelay.app import _escalation_can_work
    from hookrelay.settings import Settings

    def settings_with(**kw) -> Settings:
        base = Settings(
            config_path="unused",
            db_path=":memory:",
            plugins_dir="none",
            admin_token="a",
            read_token="r",
            max_body_bytes=1024,
            max_attempts=3,
            retention_days=0,
            alarm_url="",
            alarm_min_interval_seconds=600,
            breaker_threshold=5,
            breaker_cooldown_seconds=60,
            worker_interval_seconds=1.0,
        )
        return dataclasses.replace(base, **kw)

    feishu = _cfg({"after_minutes": 15, "send_to": ["pager"]})
    assert _escalation_can_work(settings_with(action_secret="s"), feishu), "feishu posts a callback"

    # No secret: no card carries an action of any kind.
    assert not _escalation_can_work(settings_with(), feishu)

    # A deployment reaching only channels that cannot call back, and no public
    # URL for the link that would stand in — the case this guard exists for.
    linkless = Config.from_dict(
        {
            "sources": [{"name": "grafana", "secret": "", "title": "{title}", "body": "{message}"}],
            "channels": [
                {"name": "ops-ding", "type": "dingtalk", "url": "https://ding.example/hook"},
                {"name": "pager", "type": "generic", "url": "https://pager.example/in"},
            ],
            "routes": [{"name": "all", "source": "*", "send_to": ["ops-ding"]}],
            "card_actions": {"silence": {}},
        }
    )
    assert not _escalation_can_work(settings_with(action_secret="s"), linkless)
    # ...and the same deployment once it can render a reachable link.
    assert _escalation_can_work(settings_with(action_secret="s", public_url="https://relay.example"), linkless)

    # Actions enabled nowhere: nothing is offered, so nothing can be pressed.
    no_kinds = Config.from_dict(
        {
            "sources": [{"name": "grafana", "secret": "", "title": "{title}", "body": "{message}"}],
            "channels": [{"name": "ops-feishu", "type": "feishu", "url": "https://feishu.example/hook"}],
            "routes": [{"name": "all", "source": "*", "send_to": ["ops-feishu"]}],
        }
    )
    assert not _escalation_can_work(settings_with(action_secret="s"), no_kinds)
