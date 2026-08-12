"""The brain split: transparent edge in, finished payloads out, retention.

Three promises this file pins:
  1. generic + payload:raw delivers the ORIGINAL inbound payload byte-for-
     byte in content — the brain behind the relay sees exactly what the
     monitoring system sent (transparent edge).
  2. feishu + payload:raw delivers the brain's FINISHED message untouched
     except for bot signing — interactive cards survive the hop.
  3. the ledger purges, and never deletes a promise still in flight.
"""

from __future__ import annotations

import hashlib
import hmac
import json

from hookrelay.channels import build_request
from hookrelay.config import Channel, Config
from hookrelay.pipeline import handle_hook
from hookrelay.store import Store

GRAFANA_RAW = {
    "title": "磁盘将满",
    "message": "/data 92%",
    "state": "alerting",
    "evalMatches": [{"metric": "disk", "value": 92.3}],
    "orgId": 1,
}


def _msg(payload):
    return {
        "event_id": 9,
        "source": "grafana",
        "title": "磁盘将满",
        "body": "/data 92%",
        "level": "high",
        "fields": {},
        "received_at": 1000.0,
        "payload": payload,
    }


def test_generic_raw_is_a_transparent_edge():
    channel = Channel(
        name="to-ww",
        type="generic",
        url="https://ww.example/v1/webhook/grafana",
        secret="wwsec",
        signature_header="X-Webhook-Signature",
        options={"payload": "raw"},
    )
    _url, body, headers = build_request(channel, _msg(GRAFANA_RAW), now=0.0)
    assert isinstance(body, bytes)
    # Content identical to what the monitoring system sent — nothing added,
    # nothing renamed; the brain's own adapters keep working unchanged.
    assert json.loads(body.decode()) == GRAFANA_RAW
    assert headers["X-Webhook-Signature"] == hmac.new(b"wwsec", body, hashlib.sha256).hexdigest()


def test_generic_normalized_never_leaks_the_raw_payload():
    channel = Channel(name="mirror", type="generic", url="https://m.example")
    _url, body, _headers = build_request(channel, _msg(GRAFANA_RAW), now=0.0)
    sent = json.loads(body.decode())
    assert "payload" not in sent and sent["title"] == "磁盘将满"


def test_feishu_raw_preserves_the_brains_card_and_injects_signing():
    card = {
        "msg_type": "interactive",
        "card": {
            "header": {"title": {"tag": "plain_text", "content": "事故 #12"}},
            "elements": [{"tag": "action", "actions": [{"tag": "button", "value": {"incident_id": 12}}]}],
        },
    }
    channel = Channel(
        name="feishu-out",
        type="feishu",
        url="https://open.feishu.cn/hook/x",
        secret="fsec",
        options={"payload": "raw", "payload_path": "notification"},
    )
    _url, payload, _headers = build_request(channel, _msg({"notification": card, "other": 1}), now=1700000000.0)
    assert payload["card"] == card["card"], "the interactive card must survive untouched"
    assert payload["timestamp"] == "1700000000" and payload["sign"], "bot signing injected by the sender"


async def test_raw_with_missing_path_fails_into_the_ledger():
    from hookrelay import channels as channels_mod

    channel = Channel(
        name="x", type="generic", url="https://x.example", options={"payload": "raw", "payload_path": "nope"}
    )
    ok, detail, body = await channels_mod.send(object(), channel, _msg({"there": 1}))
    assert ok is False and "yielded nothing" in detail
    assert body is None  # nothing was built, so there are no bytes to keep


async def test_pipeline_to_raw_channel_carries_original_payload(store: Store):
    cfg = Config.from_dict(
        {
            "sources": [{"name": "grafana", "secret": "", "title": "{title}", "body": "{message}"}],
            "channels": [
                {
                    "name": "to-ww",
                    "type": "generic",
                    "url": "https://ww.example/v1/webhook/grafana",
                    "options": {"payload": "raw"},
                }
            ],
            "routes": [{"name": "all", "source": "*", "send_to": ["to-ww"]}],
        }
    )
    result = await handle_hook(store, cfg, cfg.sources["grafana"], GRAFANA_RAW, now=1000.0)
    assert result["outcome"] == "routed"
    row = (await store.due_deliveries(now=1001.0))[0]
    assert json.loads(row["payload_json"]) == GRAFANA_RAW, "the delivery row carries the working copy"


async def test_retention_purges_old_but_never_in_flight(store: Store, cfg):
    old_sent = await handle_hook(store, cfg, cfg.sources["ci"], {"job": "old", "detail": "x"}, now=1000.0)
    old_stuck = await handle_hook(store, cfg, cfg.sources["ci"], {"job": "stuck", "detail": "x"}, now=1000.0)
    fresh = await handle_hook(store, cfg, cfg.sources["ci"], {"job": "fresh", "detail": "x"}, now=900_000.0)
    assert {old_sent["outcome"], old_stuck["outcome"], fresh["outcome"]} == {"routed"}

    # old_sent's delivery completes; old_stuck's stays queued (in flight).
    for row in await store.due_deliveries(now=2000.0):
        if row["event_id"] == old_sent["event_id"]:
            await store.mark_sent(row["id"], 2000.0)
    await store.add_silence("ci", until_ts=3000.0, note="expired long ago", now=1000.0)

    purged = await store.purge_older_than(cutoff=800_000.0, now=900_000.0)

    assert purged["events"] == 1, "only the fully-settled old event is purged"
    assert purged["silences"] == 1
    remaining = {event["id"] for event in await store.recent_events(10)}
    assert old_sent["event_id"] not in remaining
    assert old_stuck["event_id"] in remaining, "a queued promise is never deleted"
    assert fresh["event_id"] in remaining
