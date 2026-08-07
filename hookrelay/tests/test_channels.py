"""Wire shapes per channel — builders are pure, so no HTTP mocking needed."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from urllib.parse import parse_qs, urlparse

from hookrelay.channels import build_request

MESSAGE = {
    "event_id": 7,
    "source": "grafana",
    "title": "db down",
    "body": "primary unreachable",
    "level": "high",
    "fields": {"state": "alerting"},
    "received_at": 1000.0,
}


def test_feishu_card_shape_and_level_colour(cfg):
    url, payload, headers = build_request(cfg.channels["feishu-main"], MESSAGE, now=1700000000.0)
    assert url == "https://feishu.example/hook"
    assert payload["msg_type"] == "interactive"
    assert payload["card"]["header"]["template"] == "red"  # high earns red
    content = payload["card"]["elements"][0]["text"]["content"]
    assert "primary unreachable" in content and "**state**: alerting" in content
    # No secret configured → no signing fields.
    assert "sign" not in payload and headers == {}


def test_dingtalk_signs_the_query_string(cfg):
    now = 1700000000.0
    url, payload, _ = build_request(cfg.channels["ding-main"], MESSAGE, now=now)
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    timestamp = params["timestamp"][0]
    assert timestamp == str(int(now * 1000))
    expected = hmac.new(b"dsec", f"{timestamp}\ndsec".encode(), hashlib.sha256).digest()
    # parse_qs URL-decodes, so the value compares against the raw base64.
    assert params["sign"][0] == base64.b64encode(expected).decode()
    assert payload["msgtype"] == "markdown"
    assert "db down" in payload["markdown"]["title"]


def test_wecom_markdown_carries_title_body_fields(cfg):
    _, payload, _ = build_request(cfg.channels["wecom-main"], MESSAGE, now=0.0)
    content = payload["markdown"]["content"]
    assert content.startswith("**db down**")
    assert "primary unreachable" in content and "**state**: alerting" in content


def test_generic_forwards_the_normalized_event_signed(cfg):
    # mirror has no secret in the fixture — build a signed variant explicitly.
    from hookrelay.config import Channel

    signed = Channel(name="m2", type="generic", url="https://m2.example/in", secret="outsec")
    _, payload, headers = build_request(signed, MESSAGE, now=1700000000.0)
    # Bytes-exact: the signature covers the payload AS SENT — and, for a
    # receiver speaking our own dialect, the timestamp that makes it
    # un-replayable ("{ts}.{body}").
    assert isinstance(payload, bytes)
    assert json.loads(payload.decode()) == MESSAGE
    assert headers["X-Hook-Timestamp"] == "1700000000"
    signed_bytes = b"1700000000." + payload
    assert headers["X-Hook-Signature"] == hmac.new(b"outsec", signed_bytes, hashlib.sha256).hexdigest()


def test_foreign_receiver_keeps_the_body_only_form(cfg):
    """A receiver with its own dialect (custom signature header — e.g.
    WebhookWise's X-Webhook-Signature) must get exactly what it verifies:
    body-only, no timestamp we invented for it."""
    from hookrelay.config import Channel

    ww = Channel(
        name="ww",
        type="generic",
        url="https://ww.example/v1/webhook/grafana",
        secret="wwsec",
        signature_header="X-Webhook-Signature",
    )
    _, payload, headers = build_request(ww, MESSAGE, now=1700000000.0)
    assert "X-Hook-Timestamp" not in headers
    assert headers["X-Webhook-Signature"] == hmac.new(b"wwsec", payload, hashlib.sha256).hexdigest()
