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
                "topic": "{topic}",
            },
        },
    ],
    "channels": [
        {
            "name": "to-me",
            "type": "bridge",
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
        # The pipe asks for a method now (a channel may say PUT or PATCH);
        # this double stands in for httpx.AsyncClient, so it answers the same call.
        async def request(self, method, url, **kw):
            assert method in ("POST", "PUT", "PATCH"), method
            self.method = method
            return await self.post(url, **kw)

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
        # The pipe asks for a method now (a channel may say PUT or PATCH);
        # this double stands in for httpx.AsyncClient, so it answers the same call.
        async def request(self, method, url, **kw):
            assert method in ("POST", "PUT", "PATCH"), method
            self.method = method
            return await self.post(url, **kw)

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
                    "type": "bridge",
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


def _with_new_topics(shape: dict) -> Config:
    cfg = json.loads(json.dumps(CFG))
    cfg["pipeline"][0]["on_new_topic"] = shape
    cfg["routes"].insert(
        0,
        {"name": "topic", "source": "lark-thread", "when": {"topic": "new"}, "send_to": ["to-probe"], "priority": 110},
    )
    return Config.from_dict(cfg)


async def test_a_new_topic_is_skipped_unless_the_deployment_says_what_it_starts(store, cfg):
    result = await handle_hook(
        store,
        cfg,
        cfg.sources["lark-thread"],
        {
            "root_message_id": "om_mine",
            "topic": "new",
            "sender": "ou_sre",
            "message_id": "om_mine",
            "text": "look at node-3",
        },
        now=300.0,
    )
    assert result["outcome"] == "skipped" and result["skip_code"] == "unknown_thread"


async def test_a_new_topic_takes_the_configured_shape_and_answers_into_itself(store):
    cfg = _with_new_topics({"kind": "brief", "level": "high"})
    result = await handle_hook(
        store,
        cfg,
        cfg.sources["lark-thread"],
        {
            "root_message_id": "om_mine",
            "topic": "new",
            "sender": "ou_sre",
            "message_id": "om_mine",
            "text": "look at node-3",
        },
        now=300.0,
    )
    assert result["outcome"] == "routed" and result["channels"] == ["to-probe"]
    step = next(s for s in result["steps"] if s.get("gate") == "thread-lookup")
    assert step["result"] == "new_topic"
    ev = await store._event_row(result["event_id"])
    assert ev["level"] == "high" and ev["fields"]["kind"] == "brief" and ev["fields"]["thread_root"] == "om_mine"
    assert "session" not in ev["fields"], "a new topic has no session yet"
    # The investigator answers into the topic: its report quotes the topic event
    # and carries the root. A later reply under the person's message — whose
    # root is that message, not any card — finds the chain through the report.
    report = await handle_hook(
        store,
        cfg,
        cfg.sources["probe-notify"],
        {
            "meta": {
                "alert_name": "look at node-3 · investigation",
                "importance": "high",
                "session_key": "probe:lark-thread:9",
                "event_id": result["event_id"],
                "thread_root": "om_mine",
            },
            "report": {"summary": "nothing wrong on node-3"},
        },
        now=360.0,
    )
    # CFG's probe-notify door maps only session and correlation; the deployed
    # configs map thread_root too — written onto the row here to stand in for that.
    await store.db.execute(
        "UPDATE events SET fields_json = json_set(fields_json, '$.thread_root', ?) WHERE id = ?",
        ("om_mine", report["event_id"]),
    )
    await store.db.commit()
    found = await store.thread_context("om_mine")
    assert found is not None and found["session"] == "probe:lark-thread:9" and found["channel"] == "(topic)"
    assert found["quote"] == f"hr-{report['event_id']}" and found["origin_event_id"] == result["event_id"]
    reply = await handle_hook(
        store,
        cfg,
        cfg.sources["lark-thread"],
        {
            "root_message_id": "om_mine",
            "topic": "reply",
            "sender": "ou_sre",
            "message_id": "om_r2",
            "text": "and node-4?",
        },
        now=400.0,
    )
    assert reply["outcome"] == "routed"
    trip = await store.round_trip(result["event_id"])
    assert reply["event_id"] in [r["id"] for r in trip["returns"]], "the follow-up is a hop of the topic's chain"


# ── a question under a card that was never an investigation ───────────────────
#
# Reproduced from a real one on the work deployment, 2026-09-10 09:42: a watcher
# posted a notification, a person replied "@bot 看一下这个 ip 属于啥服务", the
# stage resolved the chain, kept `kind: follow_up`, and the routes wanted a
# `return_source` a notification does not have. Outcome `no_route`, and the
# person got silence.


async def _notification_card(store, cfg):
    """A card this pipe sent that is NOT an investigation: no session in its
    chain, which is the whole difference."""
    note = await handle_hook(
        store,
        cfg,
        cfg.sources["ww"],
        {"title": "asset beacon added 203.0.113.9", "body": "two ip assets", "level": "low"},
        now=100.0,
    )
    for row in await store.due_deliveries(now=200.0):
        if row["channel"] == "to-me":
            await store.mark_sent(row["id"], 170.0, "{}", "om_note")
    return note["event_id"]


async def test_a_question_under_a_notification_is_not_a_follow_up_to_nothing(store, cfg):
    """`follow_up` sends it to a node whose answer is "no investigation behind
    this thread" — an answer that reaches a ledger, not a person."""
    raw = CFG["pipeline"][0]
    raw["on_new_topic"] = {"kind": "task", "level": "high"}
    cfg = Config.from_dict(CFG)
    note_id = await _notification_card(store, cfg)

    result = await handle_hook(
        store,
        cfg,
        cfg.sources["lark-thread"],
        {
            "root_message_id": "om_note",
            "message_id": "om_q",
            "sender": "ou_sre",
            "topic": "reply",
            "text": "which service owns this ip?",
        },
        now=300.0,
    )
    del raw["on_new_topic"]

    assert result["outcome"] == "routed", result
    stage = next(s for s in result["steps"] if s.get("gate") == "thread-lookup")
    assert stage["result"] == "asked", "a resolve with no session is a question, not a continuation"
    assert stage["origin_event_id"] == note_id

    row = await store._event_row(result["event_id"])
    fields = row["fields"]
    assert fields["kind"] == "task" and row["level"] == "high"
    # It still belongs to the chain it was asked in.
    assert fields["thread_root"] == "om_note" and fields["correlation_id"].startswith("hr-")
    # And it carries WHAT was asked about: a person replying under a card does
    # not repeat it, so without this the question arrives with no subject.
    assert fields["about"] == "asset beacon added 203.0.113.9\n\ntwo ip assets"


async def test_the_subject_is_the_body_too_not_just_the_title(store, cfg):
    """Card #281 on the work deployment, which is why this exists.

    Its title was "four new IP assets" and the four addresses were in its BODY.
    A reply asking which service they belong to carried the title alone, so the
    question reached the investigator with every address missing — and the
    answer, accurately, was that the ticket did not list them. The model was
    right; the pipe under-carried.
    """
    raw = CFG["pipeline"][0]
    raw["on_new_topic"] = {"kind": "task", "level": "high"}
    cfg = Config.from_dict(CFG)
    note = await handle_hook(
        store,
        cfg,
        cfg.sources["ww"],
        {
            "title": "asset beacon added 4 ip assets",
            "body": "http://203.0.113.10, http://198.51.100.20, https://203.0.113.10, https://198.51.100.20",
            "level": "low",
        },
        now=100.0,
    )
    for row in await store.due_deliveries(now=200.0):
        if row["channel"] == "to-me":
            await store.mark_sent(row["id"], 170.0, "{}", "om_note281")

    result = await handle_hook(
        store,
        cfg,
        cfg.sources["lark-thread"],
        {
            "root_message_id": "om_note281",
            "message_id": "om_q281",
            "sender": "ou_sre",
            "topic": "reply",
            "text": "which service owns this ip?",
        },
        now=300.0,
    )
    del raw["on_new_topic"]

    about = (await store._event_row(result["event_id"]))["fields"]["about"]
    # The four addresses the question is ABOUT now travel with it.
    for ip in ("203.0.113.10", "198.51.100.20"):
        assert ip in about, f"{ip} did not reach the question"
    assert about.startswith("asset beacon added 4 ip assets"), "the title still leads"
    assert note["event_id"]


async def test_a_very_long_card_body_is_cut_and_says_so(store, cfg):
    """It is pasted into somebody else's prompt, so it is bounded — and a
    subject cut in half is worse than a short one when the reader cannot tell."""
    raw = CFG["pipeline"][0]
    raw["on_new_topic"] = {"kind": "task", "level": "high"}
    cfg = Config.from_dict(CFG)
    await handle_hook(
        store,
        cfg,
        cfg.sources["ww"],
        {"title": "noisy card", "body": "x" * 5000, "level": "low"},
        now=100.0,
    )
    for row in await store.due_deliveries(now=200.0):
        if row["channel"] == "to-me":
            await store.mark_sent(row["id"], 170.0, "{}", "om_long")

    result = await handle_hook(
        store,
        cfg,
        cfg.sources["lark-thread"],
        {
            "root_message_id": "om_long",
            "message_id": "om_ql",
            "sender": "ou_sre",
            "topic": "reply",
            "text": "what is this?",
        },
        now=300.0,
    )
    del raw["on_new_topic"]

    about = (await store._event_row(result["event_id"]))["fields"]["about"]
    assert len(about) < 1400, "bounded"
    assert about.endswith("(the card's body was truncated here)"), "and it admits the cut"


async def test_a_reply_that_does_have_a_session_is_still_a_follow_up(store, cfg):
    """The change must not turn every continuation into a fresh paid question."""
    raw = CFG["pipeline"][0]
    raw["on_new_topic"] = {"kind": "task", "level": "high"}
    cfg = Config.from_dict(CFG)
    await _alert_then_report(store, cfg)
    result = await handle_hook(
        store,
        cfg,
        cfg.sources["lark-thread"],
        {
            "root_message_id": "om_report",
            "message_id": "om_f",
            "sender": "ou_sre",
            "topic": "reply",
            "text": "why?",
        },
        now=300.0,
    )
    del raw["on_new_topic"]

    stage = next(s for s in result["steps"] if s.get("gate") == "thread-lookup")
    assert stage["result"] == "resolved"
    fields = (await store._event_row(result["event_id"]))["fields"]
    assert fields["kind"] == "follow_up" and fields["session"] == "probe:ww:1"
    assert "about" not in fields


async def test_without_the_knob_the_old_behaviour_stands(store, cfg):
    """A deployment that has not said what a question starts does not acquire a
    new paid door by upgrading."""
    await _notification_card(store, cfg)
    result = await handle_hook(
        store,
        cfg,
        cfg.sources["lark-thread"],
        {"root_message_id": "om_note", "message_id": "om_q2", "sender": "ou_sre", "topic": "reply", "text": "?"},
        now=300.0,
    )
    stage = next(s for s in result["steps"] if s.get("gate") == "thread-lookup")
    assert stage["result"] == "resolved"
    fields = (await store._event_row(result["event_id"]))["fields"]
    assert fields["kind"] == "follow_up" and "about" not in fields
