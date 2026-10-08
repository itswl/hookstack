"""What reached a person and what a person pressed — the weekly page's two numbers.

Pilot zero sent a person 354 cards in its loudest week and received nine
presses in its whole life, all in its first week. Those two numbers are what
the next deployment is held against, so the pipe serves them from its own
ledger: when a card went out on the channel a person reads, and when a press
came back. Never who pressed — the page counts, it does not name.
"""

from __future__ import annotations

import time
from typing import Any

import pytest


async def _sent(store: Any, channel: str, when: float, status_sent: bool = True) -> int:
    event_id = await store.insert_event(
        "grafana", f"fp-{channel}-{when}", {"title": "disk 94%", "body": "", "level": "high", "fields": {}}, "{}", when
    )
    await store.enqueue_delivery(event_id, channel, when)
    if status_sent:
        rows = await store.due_deliveries(when + 1, limit=50)
        delivery = next(r for r in rows if r["event_id"] == event_id)
        await store.mark_sent(int(delivery["id"]), when, "{}", "")
    return int(event_id)


@pytest.mark.anyio
async def test_only_a_card_on_the_channel_a_person_reads_counts(client) -> None:
    """A bridge is where a person reads; a generic channel hands the event to a
    machine. A queued card has reached nobody, and last month is not this week.
    A bridge in webhook mode returns no message id and still counts: the id is
    what /unseen needs to ask about a card, not what makes it one."""
    store = client._transport.app.state.store  # type: ignore[attr-defined]
    now = time.time()
    await _sent(store, "feishu-main", now - 3600)
    await _sent(store, "feishu-main", now - 2 * 86400)
    await _sent(store, "mirror", now - 3600)
    await _sent(store, "feishu-main", now - 60, status_sent=False)
    await _sent(store, "feishu-main", now - 9 * 86400)

    body = (await client.get("/attention", headers={"X-Read-Token": "read-t"})).json()
    assert sorted(round(now - t) for t in body["cards"]) == [3600, 2 * 86400]
    assert (body["presses"], body["people"], body["capped"]) == ([], 0, False)
    wider = (await client.get("/attention?hours=240", headers={"X-Read-Token": "read-t"})).json()
    assert len(wider["cards"]) == 3


@pytest.mark.anyio
async def test_presses_are_counted_by_kind_and_people_never_named(client) -> None:
    store = client._transport.app.state.store  # type: ignore[attr-defined]
    now = time.time()
    event_id = await _sent(store, "feishu-main", now - 7200)
    for jti, kind, actor, when in (
        ("j1", "approve", "ou_alpha", now - 3600),
        ("j2", "silence", "ou_alpha", now - 1800),
        ("j3", "useful", "ou_beta", now - 600),
        ("j4", "approve", "", now - 300),
        ("j5", "approve", "ou_gamma", now - 8 * 86400),
    ):
        await store.spend_action(jti, kind=kind, event_id=event_id, correlation_id="", actor=actor, now=when)

    body = (await client.get("/attention", headers={"X-Read-Token": "read-t"})).json()
    assert sorted(p["kind"] for p in body["presses"]) == ["approve", "approve", "silence", "useful"]
    assert body["people"] == 2, "two named actors this week; a press with no actor is still a press"
    assert "ou_" not in str(body), "the page counts people, it never names one"
    assert (await client.get("/attention")).status_code == 401
