"""Wire shapes of the generic channel — builders are pure, so no HTTP mocking needed.

The bridge's shape is in test_bridge_channel.py; the markdown dialects are plugins
(test_chat_markdown_plugins.py)."""

from __future__ import annotations

import hashlib
import hmac
import json

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
    an X-Webhook-Signature receiver) must get exactly what it verifies:
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
