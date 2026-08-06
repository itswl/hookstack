"""Fan out to several brains, gather what each sent back, compare.

The topology this file pins: one alert arrives, the SAME raw payload goes to
N processing systems, each returns its own reading through its own door, and
the ledger assembles the group. Because the input was identical, differences in
what came back are differences in JUDGEMENT — which is the whole point of
running two brains side by side.

Correlation is a CONTRACT, not a requirement: a brain that quotes back the id
it received gets grouped; one that cannot still lands in the ledger, just
unlinked. Both cases are asserted here.
"""

from __future__ import annotations

import json

import pytest

from hookrelay.config import Config
from hookrelay.pipeline import handle_hook

ALERT = {"title": "充值金额单次超500报警", "message": "用户 42 充值 920", "state": "alerting"}

# One door in, two brains out, one return door per brain. The shadow brain's
# reading deliberately does NOT continue to the chat group — comparing two
# brains must not double every notification an operator receives.
FANOUT = {
    "templates": [
        {"name": "alert-in", "title": "{title}", "body": "{message}", "level": "{state}", "level_map": {"alerting": "high"}},
        {
            "name": "brain-return",
            "title": "{meta.alert_name}",
            "body": "{meta.summary}",
            "level": "{meta.importance}",
            "fields": {"correlation_id": "{meta.correlation_id}", "brain": "{meta.brain}"},
        },
    ],
    "sources": [
        {"name": "inbound", "secret": "", "templates": ["alert-in"]},
        {"name": "ww-notify", "secret": "", "templates": ["brain-return"]},
        {"name": "lite-notify", "secret": "", "templates": ["brain-return"]},
    ],
    "channels": [
        {"name": "to-ww", "type": "generic", "url": "https://ww.example/v1/webhook", "options": {"payload": "raw"}},
        {"name": "to-lite", "type": "generic", "url": "https://lite.example/hook", "options": {"payload": "raw"}},
        {"name": "chat", "type": "generic", "url": "https://chat.example/hook"},
    ],
    "routes": [
        {"name": "fan-to-brains", "source": "inbound", "send_to": ["to-ww", "to-lite"], "priority": 100, "stop": True},
        {"name": "ww-to-chat", "source": "ww-notify", "send_to": ["chat"], "priority": 90, "stop": True},
        # lite is the shadow: gathered and compared, never delivered onward.
        {"name": "lite-compare-only", "source": "lite-notify", "send_to": ["chat"], "priority": 0, "stop": True},
    ],
}


def _return_payload(*, brain: str, importance: str, summary: str, correlation: str) -> dict:
    return {
        "meta": {
            "alert_name": ALERT["title"],
            "brain": brain,
            "importance": importance,
            "summary": summary,
            "correlation_id": correlation,
        }
    }


async def test_one_alert_fans_out_to_every_brain_with_identical_input(store):
    cfg = Config.from_dict(FANOUT)
    result = await handle_hook(store, cfg, cfg.sources["inbound"], ALERT, now=1000.0)

    assert result["channels"] == ["to-ww", "to-lite"]
    rows = await store.due_deliveries(now=1001.0)
    payloads = {row["channel"]: json.loads(row["payload_json"]) for row in rows}
    assert payloads["to-ww"] == ALERT
    assert payloads["to-lite"] == ALERT, "a fair comparison needs byte-identical input"


async def test_the_ledger_gathers_both_brains_under_one_alert(store):
    cfg = Config.from_dict(FANOUT)
    origin = await handle_hook(store, cfg, cfg.sources["inbound"], ALERT, now=1000.0)
    correlation = f"hr-{origin['event_id']}"

    # The fast brain answers in under a second; the slow one takes 47.
    await handle_hook(
        store,
        cfg,
        cfg.sources["lite-notify"],
        _return_payload(brain="ww-lite", importance="medium", summary="规则判定:金额阈值", correlation=correlation),
        now=1000.4,
    )
    await handle_hook(
        store,
        cfg,
        cfg.sources["ww-notify"],
        _return_payload(brain="webhookwise", importance="high", summary="AI:疑似批量刷单", correlation=correlation),
        now=1047.0,
    )

    trip = await store.round_trip(origin["event_id"])
    assert trip is not None
    assert trip["origin"]["title"] == ALERT["title"]
    assert [d["channel"] for d in trip["origin"]["deliveries"]] == ["to-ww", "to-lite"]

    # Both readings, side by side, with how long each took.
    by_brain = {r["fields"]["brain"]: r for r in trip["returns"]}
    assert set(by_brain) == {"ww-lite", "webhookwise"}
    assert by_brain["ww-lite"]["level"] == "medium" and by_brain["ww-lite"]["latency_seconds"] == 0.4
    assert by_brain["webhookwise"]["level"] == "high" and by_brain["webhookwise"]["latency_seconds"] == 47.0
    assert "刷单" in by_brain["webhookwise"]["body"]


async def test_the_group_assembles_from_either_end(store):
    """Asking about a RETURN gives the same group as asking about the origin —
    an operator reading the ledger arrives from either direction."""
    cfg = Config.from_dict(FANOUT)
    origin = await handle_hook(store, cfg, cfg.sources["inbound"], ALERT, now=1000.0)
    correlation = f"hr-{origin['event_id']}"
    ret = await handle_hook(
        store,
        cfg,
        cfg.sources["ww-notify"],
        _return_payload(brain="webhookwise", importance="high", summary="s", correlation=correlation),
        now=1047.0,
    )

    from_origin = await store.round_trip(origin["event_id"])
    from_return = await store.round_trip(ret["event_id"])
    assert from_return is not None and from_origin is not None
    assert from_return["origin"]["id"] == from_origin["origin"]["id"] == origin["event_id"]
    assert [r["id"] for r in from_return["returns"]] == [r["id"] for r in from_origin["returns"]]


async def test_a_brain_that_cannot_quote_still_lands_in_the_ledger(store):
    """Correlation is a contract, not a requirement: an unlinked return is
    still accounted for, it just is not gathered."""
    cfg = Config.from_dict(FANOUT)
    origin = await handle_hook(store, cfg, cfg.sources["inbound"], ALERT, now=1000.0)
    silent = await handle_hook(
        store,
        cfg,
        cfg.sources["lite-notify"],
        _return_payload(brain="mystery", importance="low", summary="s", correlation=""),
        now=1001.0,
    )

    assert silent["outcome"] == "routed", "it still routes and delivers"
    trip = await store.round_trip(origin["event_id"])
    assert trip is not None and trip["returns"] == [], "but nothing gathers it"
    assert any(e["id"] == silent["event_id"] for e in await store.recent_events(10))
    assert not any(step.get("gate") == "correlate" for step in silent["steps"])


async def test_round_trip_of_an_unknown_event_is_a_named_miss(store):
    assert await store.round_trip(999999) is None


async def test_trace_endpoint_is_read_guarded_and_assembles(client, store):
    """The HTTP surface of the comparison view."""
    assert (await client.get("/trace/1")).status_code == 401
    missing = await client.get("/trace/999999", headers={"X-Read-Token": "read-t"})
    assert missing.status_code == 404

    posted = await client.post("/hook/ci", json={"job": "build", "detail": "x"})
    event_id = posted.json()["event_id"]
    response = await client.get(f"/trace/{event_id}", headers={"X-Read-Token": "read-t"})
    assert response.status_code == 200
    body = response.json()
    assert body["origin"]["id"] == event_id and body["returns"] == []


@pytest.mark.asyncio
async def test_migration_adds_the_column_to_an_existing_ledger(tmp_path):
    """The production ledger has real events in it: CREATE TABLE IF NOT EXISTS
    would leave an old table untouched and every correlation query would fail."""
    import sqlite3

    from hookrelay.store import Store

    # Built with the stdlib driver on purpose: this stands in for a ledger
    # written by an OLDER BUILD, so it must not go through our own Store.
    path = tmp_path / "old.db"
    legacy = sqlite3.connect(str(path))
    legacy.execute(
        "CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL,"
        " received_at REAL NOT NULL, fingerprint TEXT NOT NULL, title TEXT NOT NULL,"
        " body TEXT NOT NULL DEFAULT '', level TEXT NOT NULL DEFAULT 'info',"
        " fields_json TEXT NOT NULL DEFAULT '{}', payload_json TEXT NOT NULL DEFAULT '{}')"
    )
    legacy.execute("INSERT INTO events (source, received_at, fingerprint, title) VALUES ('old', 1.0, 'fp', 'legacy')")
    legacy.commit()
    legacy.close()

    store = Store(str(path))
    await store.open()
    try:
        cursor = await store.db.execute("PRAGMA table_info(events)")
        assert "correlation_id" in {str(r["name"]) for r in await cursor.fetchall()}
        preserved = await store.recent_events(5)
        assert [e["title"] for e in preserved] == ["legacy"], "the upgrade keeps the history"
    finally:
        await store.close()
