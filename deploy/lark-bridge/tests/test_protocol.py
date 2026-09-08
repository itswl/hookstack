"""The protocol door, driven the way the pipe drives it — with the fixture the pipe's own tests assert.

deploy/lark-bridge/contract/outbound-card.json is produced by hookrelay's bridge
channel type and checked there (tests/test_bridge_channel.py); here it is what
the bridge must accept, render and send. A bridge for another platform passes
these same tests with its own renderer.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import bridge
import pytest

FIXTURE = json.loads((Path(__file__).resolve().parents[1] / "contract" / "outbound-card.json").read_text())


@pytest.fixture
def server(monkeypatch):
    sent: list[tuple[dict, str, str]] = []

    def fake_send(card: dict, reply_to: str = "", chat_id: str = "") -> tuple[bool, str]:
        sent.append((card, reply_to, chat_id))
        return True, "om_sent_1"

    monkeypatch.setattr(bridge, "send_card", fake_send)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/", sent
    srv.shutdown()


def _post(url: str, body: bytes, headers: dict[str, str]) -> tuple[int, dict]:
    request = urllib.request.Request(
        url,
        data=body,
        headers={"content-type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:  # nosec B310 — loopback test server
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


def _signed(body: bytes, ts: str | None = None, secret: str = "s3") -> dict[str, str]:
    ts = ts or str(int(time.time()))
    return {"X-Hook-Timestamp": ts, "X-Hook-Signature": bridge._sign(secret, body, ts)}


def _body(**changes) -> bytes:
    return json.dumps({**FIXTURE, **changes}, ensure_ascii=False).encode()


def test_the_fixture_is_accepted_rendered_and_sent_where_it_asks(server) -> None:
    url, sent = server
    body = _body()
    assert _post(url, body, _signed(body)) == (
        200,
        {"ok": True, "message_id": "om_sent_1"},
    )
    card, reply_to, chat_id = sent[0]
    assert (reply_to, chat_id) == (FIXTURE["reply_to"], FIXTURE["chat_id"]), "the hints are read here, never sent on"
    assert card["header"]["template"] == "red" and card["header"]["title"]["content"] == FIXTURE["card"]["title"]
    assert "lark_md" in json.dumps(card), "rendered into the dialect on this side of the seam"


def test_a_tampered_body_a_stale_stamp_or_no_signature_is_refused(server) -> None:
    url, sent = server
    body = _body()
    headers = _signed(body)
    assert _post(url, _body(chat_id="oc_example", reply_to="om_other"), headers)[0] == 401, (
        "the signature covers the bytes"
    )
    assert _post(url, body, _signed(body, ts=str(int(time.time()) - 3600)))[0] == 401
    assert _post(url, body, {})[0] == 401
    assert sent == []


def test_a_dry_run_renders_and_sends_nothing(server) -> None:
    url, sent = server
    body = _body()
    status, answer = _post(url, body, {**_signed(body), "X-Hookstack-Dry-Run": "1"})
    assert status == 200 and answer["ok"] and answer["dry_run"] and answer["message_id"] == ""
    assert answer["rendered"]["header"]["template"] == "red"
    assert sent == []


def test_a_chat_this_bridge_does_not_serve_and_a_model_that_is_not_an_object_are_refused(
    server,
) -> None:
    url, sent = server
    body = _body(chat_id="oc_stranger")
    assert _post(url, body, _signed(body))[0] == 400
    body = _body(card="not a model")
    assert _post(url, body, _signed(body))[0] == 400
    assert sent == []


def test_the_legacy_feishu_body_still_passes_through_untouched(server) -> None:
    """A `feishu`-type channel pointed at the bridge keeps working: a finished
    card with the custom-bot sign in the body, sent as it came."""
    url, sent = server
    ts = str(int(time.time()))
    sign = base64.b64encode(hmac.new(f"{ts}\n{'s3'}".encode(), b"", hashlib.sha256).digest()).decode()
    card = {
        "header": {
            "title": {"tag": "plain_text", "content": "legacy"},
            "template": "blue",
        },
        "elements": [],
    }
    body = json.dumps(
        {
            "msg_type": "interactive",
            "card": card,
            "timestamp": ts,
            "sign": sign,
            "reply_to": "om_r",
        }
    ).encode()
    assert _post(url, body, {}) == (200, {"ok": True, "message_id": "om_sent_1"})
    assert sent[0] == (card, "om_r", "")


def test_webhook_mode_posts_the_rendered_card_with_actions_as_links(server, monkeypatch) -> None:
    """The same protocol, delivered through a custom bot's incoming webhook: no
    message id comes back, hints are ignored, actions become links at the base
    the pipe named."""
    url, sent = server
    posted: list[dict] = []
    monkeypatch.setattr(bridge, "WEBHOOK_MODE", True)
    monkeypatch.setattr(bridge, "send_webhook", lambda card: posted.append(card) or (True, ""))
    # The fixture's action carries the brain's opaque value; a PIPE action carries
    # `hookrelay_action`, and only those become links (a link needs a token).
    card = {
        **FIXTURE["card"],
        "actions": [{"text": "Silence 1h", "value": {"hookrelay_action": "tok"}}],
    }
    body = _body(card=card, action_link_base="https://relay.example", chat_id="oc_anyone")
    assert _post(url, body, _signed(body)) == (200, {"ok": True, "message_id": ""})
    assert sent == [], "not sent as the app"
    (card,) = posted
    assert not [e for e in card["elements"] if e["tag"] == "action"]
    assert any("card-action?t=" in json.dumps(e) for e in card["elements"]), "actions rendered as links to the pipe"
