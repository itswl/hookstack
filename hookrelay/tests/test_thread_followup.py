"""A reply under a card finds its chain — the ledger, not the bridge, knows which.

The pipe keeps the id the IM platform gave every card it sent. A person's reply
in that card's thread arrives through a signed door carrying that id and
nothing else the pipe reads; `thread_lookup` turns it into the chain's
correlation and the investigation session the chain carries, so the follow-up
lands as a hop in the same chain and reaches the investigator addressed. A
reply under a card nobody here sent is skipped with a name.
"""

from __future__ import annotations

import json

import pytest

from hookrelay import channels
from hookrelay.config import Config
from hookrelay.pipeline import handle_hook

CFG = {
    "sources": [
        {"name": "ww", "secret": "", "title": "{title}", "body": "{body}", "level": "{level}"},
        {
            "name": "probe-notify",
            "secret": "",
            "title": "{meta.alert_name}",
            "body": "{report.summary}",
            "level": "{meta.importance}",
            "fields": {"session": "{meta.session_key}", "correlation_id": "{meta.event_id}"},
        },
        {
            "name": "lark-thread",
            "secret": "",
            "title": "{text}",
            "body": "{text}",
            "level": "info",
            "fields": {
                "root_message_id": "{root_message_id}",
                "sender": "{sender}",
                "message_id": "{message_id}",
            },
        },
    ],
    "channels": [
        {
            "name": "to-me",
            "type": "feishu",
            "url": "http://bridge/",
            "options": {"payload": "normalized", "thread_replies": True},
        },
        {"name": "to-probe", "type": "generic", "url": "http://probe/hooks/event"},
    ],
    "routes": [
        {"name": "alert", "source": "ww", "send_to": ["to-me", "to-probe"], "priority": 100},
        {"name": "report", "source": "probe-notify", "send_to": ["to-me"], "priority": 100},
        {"name": "thread", "source": "lark-thread", "send_to": ["to-probe"], "priority": 100},
    ],
    "pipeline": [
        {
            "type": "thread_lookup",
            "name": "thread-lookup",
            "when": {"source": "lark-thread"},
            "from": "root_message_id",
            "skip_code": "unknown_thread",
        },
        "routes",
    ],
}


@pytest.fixture
def cfg() -> Config:
    return Config.from_dict(CFG)


async def _alert_then_report(store, cfg):
    """An alert goes out as a card (message om_alert); its investigation returns
    and goes out as a second card (om_report). Both deliveries are marked sent
    with the platform's id, the way the worker does after a bridge answers."""
    alert = await handle_hook(
        store, cfg, cfg.sources["ww"], {"title": "disk 94%", "body": "x", "level": "high"}, now=100.0
    )
    report = await handle_hook(
        store,
        cfg,
        cfg.sources["probe-notify"],
        {
            "meta": {
                "alert_name": "disk 94% · investigation",
                "importance": "high",
                "session_key": "probe:ww:1",
                "event_id": alert["event_id"],
            },
            "report": {"summary": "the log shipper stalled"},
        },
        now=160.0,
    )
    rows = await store.due_deliveries(now=200.0)
    for row in rows:
        if row["channel"] != "to-me":
            continue
        mid = "om_alert" if row["event_id"] == alert["event_id"] else "om_report"
        await store.mark_sent(row["id"], 170.0, "{}", mid)
    return alert["event_id"], report["event_id"]


async def test_the_ledger_knows_which_chain_a_card_belongs_to(store, cfg):
    alert_id, report_id = await _alert_then_report(store, cfg)
    under_report = await store.thread_context("om_report")
    assert under_report is not None
    assert under_report["origin_event_id"] == alert_id and under_report["card_event_id"] == report_id
    assert under_report["session"] == "probe:ww:1" and under_report["quote"] == f"hr-{report_id}"
    under_alert = await store.thread_context("om_alert")
    assert under_alert is not None and under_alert["session"] == "probe:ww:1", (
        "a reply under the verdict card still finds the investigation the chain carries"
    )
    assert await store.thread_context("om_nobody") is None
    assert await store.thread_context("") is None


async def test_a_reply_under_our_card_becomes_a_hop_addressed_to_the_investigation(store, cfg):
    alert_id, report_id = await _alert_then_report(store, cfg)
    result = await handle_hook(
        store,
        cfg,
        cfg.sources["lark-thread"],
        {
            "root_message_id": "om_report",
            "sender": "ou_sre",
            "message_id": "om_reply1",
            "text": "any zombie processes on node-3?",
        },
        now=300.0,
    )
    assert result["outcome"] == "routed" and result["channels"] == ["to-probe"]
    step = next(s for s in result["steps"] if s.get("gate") == "thread-lookup")
    assert step["result"] == "resolved" and step["session"] == "probe:ww:1"
    # The follow-up is the chain's fourth hop: it quotes the report card's event,
    # and /trace from the alert now shows it among the returns.
    trip = await store.round_trip(alert_id)
    ids = [r["id"] for r in trip["returns"]]
    assert report_id in ids and result["event_id"] in ids
    reply = next(r for r in trip["returns"] if r["id"] == result["event_id"])
    assert reply["fields"]["session"] == "probe:ww:1" and reply["fields"]["thread_root"] == "om_report"
    assert reply["fields"]["kind"] == "follow_up" and reply["fields"]["sender"] == "ou_sre"
    assert reply["fields"]["correlation_id"] == f"hr-{report_id}"
    assert reply["fields"]["return_source"] == "probe-notify", (
        "the door the report came through names the node to route to"
    )


async def test_a_reply_under_a_card_nobody_here_sent_is_skipped_with_a_name(store, cfg):
    result = await handle_hook(
        store,
        cfg,
        cfg.sources["lark-thread"],
        {"root_message_id": "om_elsewhere", "sender": "ou_sre", "message_id": "om_reply2", "text": "hello?"},
        now=300.0,
    )
    assert result["outcome"] == "skipped" and result["skip_code"] == "unknown_thread"
    assert await store.due_deliveries(now=400.0) == []


async def test_a_message_from_another_door_is_left_alone(store, cfg):
    result = await handle_hook(store, cfg, cfg.sources["ww"], {"title": "cpu", "body": "x", "level": "high"}, now=100.0)
    step = next(s for s in result["steps"] if s.get("gate") == "thread-lookup")
    assert step["result"] == "not_applied" and result["outcome"] == "routed"


async def test_send_keeps_the_platform_id_and_asks_for_a_thread_reply(cfg):
    captured: dict = {}

    class Response:
        status_code = 200
        text = ""

        def json(self):
            return {"ok": True, "message_id": "om_new"}

    class Client:
        async def post(self, url, content=None, headers=None):
            captured["content"] = content
            return Response()

    base = {
        "event_id": 7,
        "source": "probe-notify",
        "title": "disk 94% · investigation",
        "body": "answer",
        "level": "high",
        "received_at": 0.0,
        "payload": {},
    }
    ok, detail, body, mid = await channels.send(Client(), cfg.channels["to-me"], {**base, "fields": {}})
    assert ok and mid == "om_new" and "reply_to" not in json.loads(body)
    ok, detail, body, mid = await channels.send(
        Client(), cfg.channels["to-me"], {**base, "fields": {"thread_root": "om_report"}}
    )
    assert ok and json.loads(body)["reply_to"] == "om_report", "a follow-up's answer goes back into its thread"
    # A channel that never said it could reply is not asked to: a custom-bot
    # webhook would only be confused by the key.
    ok, detail, body, mid = await channels.send(
        Client(), cfg.channels["to-probe"], {**base, "fields": {"thread_root": "om_report"}}
    )
    assert ok and "reply_to" not in json.loads(body)


async def test_a_channel_can_name_the_chat_one_bridge_serves_several(cfg):
    captured: dict = {}

    class Response:
        status_code = 200
        text = ""

        def json(self):
            return {"ok": True, "message_id": "om_new"}

    class Client:
        async def post(self, url, content=None, headers=None):
            captured["content"] = content
            return Response()

    two = Config.from_dict(
        {
            **CFG,
            "channels": [
                *CFG["channels"],
                {
                    "name": "to-plan-chat",
                    "type": "feishu",
                    "url": "http://bridge/",
                    "options": {"payload": "normalized", "thread_replies": True, "chat_id": "oc_plan"},
                },
            ],
        }
    )
    message = {
        "event_id": 7,
        "source": "ww",
        "title": "t",
        "body": "b",
        "level": "high",
        "received_at": 0.0,
        "payload": {},
        "fields": {},
    }
    ok, _, body, _ = await channels.send(Client(), two.channels["to-plan-chat"], message)
    assert ok and json.loads(body)["chat_id"] == "oc_plan"
    ok, _, body, _ = await channels.send(Client(), two.channels["to-me"], message)
    assert ok and "chat_id" not in json.loads(body), "a channel without the option asks for nothing"


async def test_lark_api_shape_is_understood_too():
    assert channels._platform_message_id({"code": 0, "data": {"message_id": "om_x"}}) == "om_x"
    assert channels._platform_message_id({"ok": True}) == ""
    assert channels._platform_message_id("nonsense") == ""
