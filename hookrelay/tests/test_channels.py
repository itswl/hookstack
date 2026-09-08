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


async def test_a_delivery_carries_the_chain_handle_a_brain_will_be_identified_by():
    """`X-Hook-Correlation-Id` on every delivery, in headers and never in the body.

    Half of a seam: the receiving side (hookprobe's event door) adopts this as
    the id of the piece of WORK the run belongs to, so an alert, its verdict and
    the investigation that came back share one id with nothing new configured.
    That side asserts it reads the header; without this, nothing asserted the
    pipe still sends it, and a seam checked from one end is not checked.

    Headers, not body: the signature covers the payload as sent, and a
    correlation stamped into it would have to be signed around.
    """
    from hookrelay import channels
    from hookrelay.config import Channel

    seen: dict[str, object] = {}

    class Recorder:
        async def post(self, url, content, headers):
            seen.update({"url": url, "headers": headers, "content": content})

            class Response:
                status_code = 200
                text = "{}"

                @staticmethod
                def json():
                    return {}

            return Response()

    channel = Channel(name="to-probe", type="generic", url="https://probe.example/hooks/event", secret="s")
    message = {**MESSAGE, "_correlation_id": "hr-1887", "_idempotency_key": "1887:to-probe"}
    ok, _detail, body, _platform_id = await channels.send(Recorder(), channel, message)

    assert ok
    assert seen["headers"]["X-Hook-Correlation-Id"] == "hr-1887"
    assert seen["headers"]["X-Request-Id"] == "hr-1887", "allowlist-minded receivers keep this one"
    assert seen["headers"]["X-Hook-Idempotency-Key"] == "1887:to-probe"
    assert b"_correlation_id" not in (body or b""), "transport, not content: it must not enter the signed body"
