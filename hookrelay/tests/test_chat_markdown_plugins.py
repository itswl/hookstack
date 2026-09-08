"""DingTalk and WeCom, rendered by the shipped plugin from the pipe's card model.

The assertions that lived in test_channels.py and test_processed_render.py when
these were built-in types, moved with them. A shipped example that is not tested
rots into a lie.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from test_processed_render import RESULT, _message

from hookrelay import actions, registry
from hookrelay.channels import build_request
from hookrelay.config import Channel

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "plugins"
MESSAGE = {
    "event_id": 7,
    "source": "grafana",
    "title": "db down",
    "body": "primary unreachable",
    "level": "high",
    "fields": {"state": "alerting"},
    "received_at": 1000.0,
}


@pytest.fixture(scope="module", autouse=True)
def load_example_plugins():
    if "dingtalk" not in registry.CHANNEL_BUILDERS:
        registry.load_plugins(EXAMPLES)


def _channel(kind: str, **options) -> Channel:
    return Channel(name=f"{kind}-out", type=kind, url=f"https://{kind}.example/hook", options=options)


def test_the_plugins_declare_that_their_channels_carry_links():
    assert registry.channel_can("dingtalk", "links") and registry.channel_can("wecom", "links")
    assert not registry.channel_can("dingtalk", "callbacks"), "a webhook robot cannot call back"


def test_dingtalk_signs_the_query_string_and_renders_markdown():
    now = 1700000000.0
    signed = Channel(name="ding", type="dingtalk", url="https://ding.example/hook", secret="dsec")
    url, payload, _ = build_request(signed, MESSAGE, now=now)
    params = parse_qs(urlparse(url).query)
    timestamp = params["timestamp"][0]
    assert timestamp == str(int(now * 1000))
    expected = hmac.new(b"dsec", f"{timestamp}\ndsec".encode(), hashlib.sha256).digest()
    assert params["sign"][0] == base64.b64encode(expected).decode()  # parse_qs URL-decodes
    assert payload["msgtype"] == "markdown" and "db down" in payload["markdown"]["title"]
    assert payload["markdown"]["text"].startswith("### db down")
    assert "primary unreachable" in payload["markdown"]["text"] and "**state**: alerting" in payload["markdown"]["text"]


def test_wecom_markdown_carries_title_body_fields():
    _, payload, _ = build_request(_channel("wecom"), MESSAGE, now=0.0)
    content = payload["markdown"]["content"]
    assert content.startswith("**db down**")
    assert "primary unreachable" in content and "**state**: alerting" in content


def test_a_judgement_gets_its_own_markdown_without_dead_buttons():
    _url, ding, _h = build_request(_channel("dingtalk", payload="processed"), _message(), now=1700000000.0)
    text = ding["markdown"]["text"]
    assert text.startswith("### 📡 Single top-up over 500"), "DingTalk wants a heading"
    assert "🔴 HIGH" in text and "three large top-ups" in text
    assert "[Large top-up runbook](https://kb.example/runbook/42)" in text, "links survive where buttons cannot"
    assert "opaque-token" not in text, "a button with no callback channel is worse than none"
    _url, wecom, _h = build_request(_channel("wecom", payload="processed"), _message(), now=0.0)
    content = wecom["markdown"]["content"]
    assert content.startswith("**📡 Single top-up over 500**"), "WeCom renders bold, not #"
    assert "**Impact**" in content and "> grafana · business" in content


def test_pipe_actions_become_links_only_where_a_link_can_land(monkeypatch):
    """A webhook robot cannot call back, so an action is a link to the pipe's
    confirm page — at the base the channel names, else HOOKRELAY_PUBLIC_URL,
    else nothing: a link nobody can reach is worse than no link."""
    monkeypatch.delenv("HOOKRELAY_PUBLIC_URL", raising=False)
    minted = actions.offered(
        "card-s3cret",
        [{"kind": "silence", "text": "Silence 1h", "minutes": 60}],
        {"silence": {"params": {}}},
        event_id=4,
        correlation_id="hr-4",
        now=time.time(),
    )
    message = _message({**RESULT, "actions": minted})
    with_base = _channel("dingtalk", payload="processed", action_link_base="https://relay.example/")
    assert (
        "[Silence 1h](https://relay.example/card-action?t="
        in build_request(with_base, message, now=0.0)[1]["markdown"]["text"]
    )
    assert (
        "card-action"
        not in build_request(_channel("dingtalk", payload="processed"), message, now=0.0)[1]["markdown"]["text"]
    )
    monkeypatch.setenv("HOOKRELAY_PUBLIC_URL", "https://env.example")
    assert (
        "https://env.example/card-action?t="
        in build_request(_channel("wecom", payload="processed"), message, now=0.0)[1]["markdown"]["content"]
    )


def test_payload_text_cannot_smuggle_markup_or_hijack_a_link():
    hostile = {**MESSAGE, "title": "<at id=all></at> disk nominal", "fields": {"note": "[x](https://evil.example)"}}
    text = build_request(_channel("dingtalk"), hostile, now=0.0)[1]["markdown"]["text"]
    assert "\\<at id=all>\\</at> disk nominal" in text and "\\[x\\](https://evil.example)" in text
    result = {**RESULT, "links": [{"text": "Runbook](https://evil.example) click", "url": "https://kb.example/ok"}]}
    text = build_request(_channel("wecom", payload="processed"), _message(result), now=0.0)[1]["markdown"]["content"]
    assert "](https://kb.example/ok)" in text and "Runbook\\](https://evil.example) click" in text
