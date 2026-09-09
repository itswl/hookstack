"""Which cards nobody opened — the difference between two kinds of waiting.

A board that says something is waiting cannot say WHICH waiting it is: a
decision nobody has made, or a card nobody has seen. Those need different
things from a person, and until this they were the same row.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

import httpx
import pytest

from hookrelay import channels
from hookrelay.config import Channel


async def _sent_card(client: httpx.AsyncClient, app_store: Any, message_id: str, when: float) -> int:
    """One delivered card on the bridge channel, with the id a platform gave it."""
    event_id = await app_store.insert_event(
        "grafana", "fp-" + message_id, {"title": "disk 94%", "body": "", "level": "high", "fields": {}}, "{}", when
    )
    await app_store.enqueue_delivery(event_id, "feishu-main", when)
    rows = await app_store.due_deliveries(when + 1, limit=10)
    delivery = next(r for r in rows if r["event_id"] == event_id)
    await app_store.mark_sent(int(delivery["id"]), when, None, message_id)
    return int(event_id)


@pytest.mark.anyio
async def test_seen_unseen_and_cannot_tell_are_three_answers(client, monkeypatch) -> None:
    """The third one is the point. An unknown reported as unseen would be this
    feature telling the exact lie it exists to stop."""
    store = client._transport.app.state.store  # type: ignore[attr-defined]
    now = time.time()
    read_id = await _sent_card(client, store, "om_read", now - 3600)
    unread_id = await _sent_card(client, store, "om_unread", now - 7200)
    await _sent_card(client, store, "om_gone", now - 60)

    async def answers(_client: Any, _channel: Channel, ids: list[str], _now: float) -> dict[str, Any]:
        return {
            "om_read": {"readers": 2, "first_read_at": now - 3500},
            "om_unread": {"readers": 0, "first_read_at": None},
            "om_gone": {"error": "message not found"},
        }

    monkeypatch.setattr(channels, "ask_read_status", answers)
    body = (await client.get("/unseen", headers={"X-Read-Token": "read-t"})).json()

    assert (body["seen"], body["unseen"], body["unknown"]) == (1, 1, 1)
    assert [c["event_id"] for c in body["cards"]] == [unread_id]
    assert body["cards"][0]["title"] == "disk 94%"
    assert body["cards"][0]["waiting_hours"] == pytest.approx(2.0, abs=0.1)
    assert read_id not in [c["event_id"] for c in body["cards"]]


@pytest.mark.anyio
async def test_a_bridge_that_cannot_be_asked_is_unknown_not_unread(client, monkeypatch) -> None:
    """A bridge that is down must not be reported as a room full of people
    ignoring you."""
    store = client._transport.app.state.store  # type: ignore[attr-defined]
    await _sent_card(client, store, "om_1", time.time() - 600)

    async def unreachable(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(channels, "ask_read_status", unreachable)
    body = (await client.get("/unseen", headers={"X-Read-Token": "read-t"})).json()
    assert (body["seen"], body["unseen"], body["unknown"]) == (0, 0, 1)
    assert body["cards"] == []


@pytest.mark.anyio
async def test_a_delivery_with_no_platform_id_is_never_counted(client) -> None:
    """A card the platform gave no id for is not one nobody read — it is one
    nothing can be asked about, and counting it as unseen would invent alarm."""
    store = client._transport.app.state.store  # type: ignore[attr-defined]
    now = time.time()
    extracted = {"title": "build", "body": "", "level": "high", "fields": {}}
    event_id = await store.insert_event("ci", "fp-x", extracted, "{}", now)
    await store.enqueue_delivery(event_id, "feishu-main", now)
    row = next(r for r in await store.due_deliveries(now + 1, limit=10) if r["event_id"] == event_id)
    await store.mark_sent(int(row["id"]), now, None, None)

    body = (await client.get("/unseen", headers={"X-Read-Token": "read-t"})).json()
    assert body["checked"] == 0


@pytest.mark.anyio
async def test_the_read_question_is_signed_like_everything_else(client) -> None:
    """The bridge refuses an unsigned card; a read query is no different."""
    seen: dict[str, Any] = {}

    class _Client:
        async def post(self, url: str, content: bytes, headers: dict[str, str], timeout: float = 0.0) -> Any:
            seen.update(url=url, body=content, headers=headers)
            return httpx.Response(200, json={"ok": True, "supported": True, "read": {"om_1": {"readers": 1}}})

    channel = Channel(name="chat", type="bridge", url="http://bridge:9100/", secret="sec")
    out = await channels.ask_read_status(_Client(), channel, ["om_1"], now=1000.0)
    assert out == {"om_1": {"readers": 1}}

    stamp = seen["headers"]["X-Hook-Timestamp"]
    expected = hmac.new(b"sec", stamp.encode() + b"." + seen["body"], hashlib.sha256).hexdigest()
    assert seen["headers"]["X-Hook-Signature"] == expected
    assert json.loads(seen["body"])["read"]["message_ids"] == ["om_1"]


@pytest.mark.anyio
async def test_the_batch_is_bounded_before_it_leaves(client) -> None:
    """One platform call PER ID at the far end. The first version asked about 59
    cards at once, the bridge died mid-answer with a broken pipe, and every card
    came back "unknown" — the honest classification of a question nobody
    finished asking, and useless as an answer."""
    asked: dict[str, Any] = {}

    class _Client:
        async def post(self, url: str, content: bytes, headers: dict[str, str], timeout: float = 0.0) -> Any:
            asked.update(ids=json.loads(content)["read"]["message_ids"], timeout=timeout)
            return httpx.Response(200, json={"ok": True, "read": {}})

    channel = Channel(name="chat", type="bridge", url="http://bridge:9100/")
    await channels.ask_read_status(_Client(), channel, [f"om_{i}" for i in range(100)], now=0.0)
    assert len(asked["ids"]) == channels.READ_BATCH_MAX
    assert asked["timeout"] > 10.0, "the shared delivery timeout is not enough for a per-id question"


@pytest.mark.anyio
async def test_an_unauthenticated_read_is_refused(client) -> None:
    assert (await client.get("/unseen")).status_code == 401
