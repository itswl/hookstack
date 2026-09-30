"""One alert's journey from any handle it left behind.

The pipe resolves the handle from its own ledger and reads nothing inside a
node: every handle here is one it minted or copied — the event id, the
`hr-<id>` it stamps on egress, the session key and work id a return door
extracted into fields, the platform id of a card it sent. And the journey ends
with what the condition did afterwards — a stated recovery, a re-fire — because
that is what every verdict on a fix is measured against.
"""

from __future__ import annotations

from pathlib import Path

from hookrelay.config import Config
from hookrelay.pipeline import handle_hook
from hookrelay.timeline import render as render_timeline

JOURNEY = {
    "sources": [
        {
            "name": "inbound",
            "secret": "",
            "title": "{title}",
            "body": "{message}",
            "level": "{state}",
            "level_map": {"alerting": "high", "resolved": "info"},
            "recovery": "{state}",
            "fields": {"state": "{state}"},
            "fingerprint_fields": ["title", "state"],
            "dedup_window_seconds": 600,
        },
        {
            "name": "probe-notify",
            "secret": "",
            "title": "{meta.alert_name}",
            "body": "{analysis.summary}",
            "level": "{meta.importance}",
            "fields": {
                "session": "{meta.session_key}",
                "work_id": "{meta.work_id}",
                "cost_usd": "{meta.cost_usd}",
                "correlation_id": "{meta.event_id}",
            },
        },
    ],
    "channels": [
        {"name": "to-probe", "type": "generic", "url": "https://probe.example/hooks/event"},
        {"name": "chat", "type": "bridge", "url": "https://bridge.example/send"},
    ],
    "routes": [
        {"name": "report-to-chat", "source": "probe-notify", "send_to": ["chat"], "priority": 100, "stop": True},
        {"name": "alert-out", "source": "inbound", "send_to": ["to-probe", "chat"], "priority": 50, "stop": True},
    ],
}
ALERT = {"title": "disk 94% on node-3", "message": "7% free", "state": "alerting"}


def _report(event_id: int, *, session: str, work_id: str) -> dict:
    return {
        "meta": {
            "alert_name": "disk 94% on node-3 · investigation",
            "importance": "high",
            "event_id": event_id,
            "session_key": session,
            "work_id": work_id,
            "cost_usd": 0.42,
            "brain": "hookprobe",
        },
        "analysis": {"summary": "the log volume filled /data", "event_type": "investigation"},
    }


async def test_every_handle_the_journey_left_behind_finds_the_same_origin(store):
    cfg = Config.from_dict(JOURNEY)
    origin = await handle_hook(store, cfg, cfg.sources["inbound"], ALERT, now=1000.0)
    alert_id = origin["event_id"]
    report = await handle_hook(
        store,
        cfg,
        cfg.sources["probe-notify"],
        _report(alert_id, session=f"probe:inbound:{alert_id}", work_id="w-77"),
        now=1030.0,
    )
    # The card the pipe sent for the report, with the id the platform gave it.
    queued = await store.due_deliveries(now=2000.0)
    card = next(d for d in queued if d["event_id"] == report["event_id"] and d["channel"] == "chat")
    await store.mark_sent(card["id"], 1031.0, '{"card":{"actions":[{"text":"Ask why"}]}}', "om_card_1")

    handles = (
        str(alert_id),
        f"hr-{alert_id}",
        f"probe:inbound:{alert_id}",
        "w-77",
        "om_card_1",
        str(report["event_id"]),
        # A run whose report never came home still finds its alert: the key the
        # investigator builds embeds the event id it was handed.
        f"probe:another-door:{alert_id}",
    )
    for handle in handles:
        resolved = await store.resolve_ref(handle)
        assert resolved is not None, handle
        trip = await store.round_trip(resolved)
        assert trip is not None and trip["origin"]["id"] == alert_id, handle
    assert (await store.round_trip(await store.resolve_ref("om_card_1")))["origin"]["deliveries"][1][
        "platform_message_id"
    ] is None, "the alert's own card was never marked sent here"

    for miss in ("", "   ", "nothing-like-this", "om_never_sent", "probe:only-one-colon"):
        assert await store.resolve_ref(miss) is None, miss


async def test_the_journey_ends_with_what_the_condition_did_afterwards(store):
    """A stated recovery and a re-fire of the same source and title, within a
    day; nothing else's, and nothing from beyond the window."""
    cfg = Config.from_dict(JOURNEY)
    origin = await handle_hook(store, cfg, cfg.sources["inbound"], ALERT, now=1000.0)
    again = await handle_hook(store, cfg, cfg.sources["inbound"], ALERT, now=1100.0)
    assert again["outcome"] == "skipped" and again["skip_code"] == "duplicate", "inside the dedup window"
    await handle_hook(store, cfg, cfg.sources["inbound"], dict(ALERT, title="cpu"), now=1150.0)
    ended = await handle_hook(store, cfg, cfg.sources["inbound"], dict(ALERT, state="resolved"), now=1200.0)
    await handle_hook(store, cfg, cfg.sources["inbound"], ALERT, now=1000.0 + 2 * 86400)

    trip = await store.round_trip(origin["event_id"])
    assert trip is not None
    assert [r["id"] for r in trip["recoveries"]] == [ended["event_id"]]
    assert trip["recoveries"][0]["latency_seconds"] == 200.0 and trip["recoveries"][0]["outcome"] == "routed"
    assert [r["id"] for r in trip["refires"]] == [again["event_id"]]
    assert trip["refires"][0]["skip_code"] == "duplicate"
    assert trip["origin"]["is_recovery"] in (0, False, None) and trip["human_actions"] == []


# deploy/work.yaml's two doors below the click: the planner posts the plan a
# person handed off, with its session and work item and no quote, and the work
# runner reports on what it did.
HANDOFF = {
    "sources": [
        *JOURNEY["sources"],
        {
            "name": "plan-approved",
            "secret": "",
            "title": "{title}",
            "body": "{message}",
            "level": "high",
            "fields": {"session": "{session}", "work_id": "{work_id}", "kind": "brief"},
        },
        {**JOURNEY["sources"][1], "name": "work-notify"},
    ],
    "channels": [*JOURNEY["channels"], {"name": "to-work", "type": "generic", "url": "https://work.example/hooks"}],
    "routes": [
        *JOURNEY["routes"],
        {"name": "act-on-it", "source": "plan-approved", "send_to": ["to-work"], "priority": 100, "stop": True},
        {"name": "work-back", "source": "work-notify", "send_to": ["chat"], "priority": 100, "stop": True},
    ],
}


async def test_a_plan_handed_off_is_one_journey_with_the_work_it_started(store):
    """The handoff quotes nothing, but it carries the plan's work item, the
    `hr-<alert>` the pipe minted. Read by quotes alone, the work run sat in a
    chain of its own beside the request it carried out, and the board showed
    one piece of work as two rows (twice on 2026-09-30)."""
    cfg = Config.from_dict(HANDOFF)
    alert_id = (await handle_hook(store, cfg, cfg.sources["inbound"], ALERT, now=1000.0))["event_id"]
    plan_session, work_id = f"probe:inbound:{alert_id}", f"hr-{alert_id}"
    plan = await handle_hook(
        store, cfg, cfg.sources["probe-notify"], _report(alert_id, session=plan_session, work_id=work_id), now=1030.0
    )
    brief = {"title": f"plan handed off: {plan_session}", "message": "do it", "session": plan_session}
    handoff = await handle_hook(store, cfg, cfg.sources["plan-approved"], {**brief, "work_id": work_id}, now=1100.0)
    work_session = f"probe:plan-approved:{handoff['event_id']}"
    work = await handle_hook(
        store,
        cfg,
        cfg.sources["work-notify"],
        _report(handoff["event_id"], session=work_session, work_id=work_id),
        now=1200.0,
    )
    for event_id, card_id in ((plan["event_id"], "om_plan"), (work["event_id"], "om_work")):
        queued = await store.due_deliveries(now=2000.0)
        card = next(d for d in queued if d["event_id"] == event_id and d["channel"] == "chat")
        await store.mark_sent(card["id"], 1300.0, "{}", card_id)

    trip = await store.round_trip(alert_id)
    assert trip is not None
    assert [r["id"] for r in trip["returns"]] == [plan["event_id"], handoff["event_id"], work["event_id"]]
    for handle in (str(work["event_id"]), work_session, str(handoff["event_id"])):
        resolved = await store.resolve_ref(handle)
        assert resolved is not None and (await store.round_trip(resolved))["origin"]["id"] == alert_id, handle
    chains = render_timeline(await store.recent_events(10))["chains"]
    assert [c["chain"] for c in chains] == [str(alert_id)], "one row on the board"
    assert [h["by_work"] for h in chains[0]["hops"]] == [False, False, True, False]

    # A reply stays in its own conversation: under the plan's card it goes on
    # with the planner, never with the node that holds a write credential.
    assert (await store.thread_context("om_plan") or {}).get("session") == plan_session
    assert (await store.thread_context("om_work") or {}).get("session") == work_session

    # A work item the pipe did not mint names no chain: a console plan hands
    # off under its own session key.
    console = {**brief, "title": "plan handed off: probe:console:3", "work_id": "probe:console:3"}
    alone = await handle_hook(store, cfg, cfg.sources["plan-approved"], console, now=1400.0)
    assert (await store.round_trip(alone["event_id"]) or {}).get("origin", {}).get("id") == alone["event_id"]


async def test_trace_answers_a_handle_over_http(client):
    posted = await client.post("/hook/ci", json={"job": "build", "detail": "x"})
    event_id = posted.json()["event_id"]
    for handle in (str(event_id), f"hr-{event_id}", f"probe:ci:{event_id}"):
        response = await client.get(f"/trace/{handle}", headers={"X-Read-Token": "read-t"})
        assert response.status_code == 200, handle
        body = response.json()
        assert body["origin"]["id"] == event_id and body["recoveries"] == [] and body["refires"] == []
    assert (await client.get("/trace/om_nothing", headers={"X-Read-Token": "read-t"})).status_code == 404
    assert (await client.get("/trace/hr-1")).status_code == 401, "the read guard covers every spelling"


def test_the_board_reads_the_journey_from_a_handle():
    """The page half of the contract: an alert's story opens from a deep link,
    the older `#journey=` links other pages and cards still carry keep working,
    and the handle goes to /trace untouched — the ledger does the resolving."""
    page = Path(__file__).resolve().parents[1].joinpath("hookrelay", "status.html").read_text(encoding="utf-8")
    assert 'id="drawer"' in page and '"#/alert/"' in page
    assert "^journey=(.+)$" in page, "the links already out there must still open the alert"
    assert 'api("/trace/" + encodeURIComponent(ref))' in page and "fetch(BASE + path" in page
    assert "setInterval" not in page, "boards are pushed, not polled"
