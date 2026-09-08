"""The bridge channel type: a card MODEL, signed like everything the pipe sends.

This is the builder that does not know what a Feishu card looks like. What it
emits is pinned as deploy/lark-bridge/contract/outbound-card.json — the same
file the bridge's own tests accept, render and send — so the seam is checked
from both ends and a change to either side shows up as a fixture diff.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import pytest
from test_processed_render import _message

from hookrelay import registry
from hookrelay.channels import BRIDGE_PROTOCOL, build_request
from hookrelay.config import Channel

FIXTURE = Path(__file__).resolve().parents[2] / "deploy" / "lark-bridge" / "contract" / "outbound-card.json"


def test_the_envelope_is_the_documented_fixture_and_knows_no_dialect() -> None:
    channel = Channel(
        name="chat",
        type="bridge",
        url="http://bridge:9100/",
        options={"payload": "processed", "thread_replies": True, "chat_id": "oc_example"},
    )
    message = _message()
    message["fields"] = {"thread_root": "om_example_root"}
    _url, body, headers = build_request(channel, message, now=0.0)
    envelope = json.loads(body)
    assert envelope == json.loads(FIXTURE.read_text()), (
        "the bridge is tested against this fixture — change both together"
    )
    assert envelope["protocol"] == BRIDGE_PROTOCOL
    assert (envelope["reply_to"], envelope["chat_id"]) == ("om_example_root", "oc_example")
    card = envelope["card"]
    assert card["tone"] == "high" and card["title"].endswith("Single top-up over 500") and card["links"]
    for dialect in ("lark_md", "msg_type", "template", "plain_text"):
        assert dialect not in body.decode(), f"{dialect!r} is the bridge's business"
    assert "X-Hook-Signature" not in headers, "no secret, no signature"


def test_the_signature_covers_the_exact_bytes_including_the_thread_hints() -> None:
    channel = Channel(
        name="chat", type="bridge", url="http://bridge:9100/", secret="s3", options={"thread_replies": True}
    )
    message = {
        "event_id": 7,
        "source": "probe-notify",
        "title": "report",
        "body": "…",
        "level": "high",
        "fields": {"thread_root": "om_root"},
    }
    _url, body, headers = build_request(channel, message, now=1700000000.0)
    assert headers["X-Hook-Timestamp"] == "1700000000"
    assert headers["X-Hook-Signature"] == hmac.new(b"s3", b"1700000000." + body, hashlib.sha256).hexdigest()
    assert json.loads(body)["reply_to"] == "om_root", "the hint is inside the signed bytes, not bolted on after"


def test_a_plain_event_becomes_a_model_too() -> None:
    channel = Channel(name="chat", type="bridge", url="http://bridge:9100/")
    message = {
        "event_id": 7,
        "source": "alertmanager",
        "title": "disk 94%",
        "body": "7% free",
        "level": "high",
        "fields": {"host": "node-3", "empty": ""},
    }
    _url, body, _headers = build_request(channel, message, now=0.0)
    assert json.loads(body)["card"] == {
        "title": "disk 94%",
        "tone": "high",
        "summary": "7% free",
        "details": "host: node-3",
        "footer": "hookrelay · alertmanager · #7",
    }


def test_a_finished_platform_payload_is_refused_by_name() -> None:
    channel = Channel(name="chat", type="bridge", url="http://bridge:9100/", options={"payload": "raw"})
    with pytest.raises(ValueError, match="card model"):
        build_request(channel, {"payload": {"msg_type": "interactive", "card": {}}}, now=0.0)


def test_what_a_person_can_do_from_a_channel_is_declared_not_named() -> None:
    assert registry.channel_can("bridge", "callbacks") and registry.channel_can("feishu", "callbacks")
    assert registry.channel_can("dingtalk", "links") and registry.channel_can("wecom", "links")
    assert not registry.channel_can("generic", "callbacks") and not registry.channel_can("unknown", "links")
