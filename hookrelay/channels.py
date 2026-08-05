"""Channel adapters: pure request builders plus one thin sender.

Builders return (url, json_payload, headers) and touch no network, so tests
assert exact wire shapes without mocking HTTP. The sender is the only place
that talks to the world.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any
from urllib.parse import quote_plus

import httpx

from hookrelay.config import Channel

# Feishu card header colours by normalized level. Red is reserved for high —
# the family colour doctrine: alarm colours are earned, not decorative.
_FEISHU_LEVEL_COLOR = {
    "critical": "red",
    "high": "red",
    "warning": "orange",
    "medium": "orange",
    "low": "wathet",
    "info": "blue",
}


def _fields_lines(message: dict[str, Any]) -> str:
    fields = message.get("fields") or {}
    return "\n".join(f"**{name}**: {value}" for name, value in fields.items() if value)


def build_feishu(channel: Channel, message: dict[str, Any], now: float) -> tuple[str, dict[str, Any], dict[str, str]]:
    color = _FEISHU_LEVEL_COLOR.get(str(message.get("level", "info")), "turquoise")
    body_lines = [part for part in (message.get("body", ""), _fields_lines(message)) if part]
    elements: list[dict[str, Any]] = [
        {"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(body_lines) or "(no body)"}},
        {
            "tag": "note",
            "elements": [{"tag": "plain_text", "content": f"hookrelay · {message['source']} · #{message['event_id']}"}],
        },
    ]
    payload: dict[str, Any] = {
        "msg_type": "interactive",
        "card": {
            "header": {"title": {"tag": "plain_text", "content": message["title"]}, "template": color},
            "elements": elements,
        },
    }
    # Feishu custom-bot signing: base64(HMAC-SHA256(key="{ts}\n{secret}", msg="")).
    if channel.secret:
        timestamp = str(int(now))
        key = f"{timestamp}\n{channel.secret}".encode()
        sign = base64.b64encode(hmac.new(key, b"", hashlib.sha256).digest()).decode()
        payload["timestamp"] = timestamp
        payload["sign"] = sign
    return channel.url, payload, {}


def build_dingtalk(channel: Channel, message: dict[str, Any], now: float) -> tuple[str, dict[str, Any], dict[str, str]]:
    lines = [f"### {message['title']}"]
    if message.get("body"):
        lines.append(str(message["body"]))
    fields = _fields_lines(message)
    if fields:
        lines.append(fields)
    lines.append(f"> hookrelay · {message['source']} · #{message['event_id']}")
    payload = {"msgtype": "markdown", "markdown": {"title": message["title"], "text": "\n\n".join(lines)}}
    url = channel.url
    # DingTalk signing rides the query string: sign of "{ts_ms}\n{secret}".
    if channel.secret:
        timestamp = str(int(now * 1000))
        digest = hmac.new(channel.secret.encode(), f"{timestamp}\n{channel.secret}".encode(), hashlib.sha256).digest()
        sign = quote_plus(base64.b64encode(digest))
        joiner = "&" if "?" in url else "?"
        url = f"{url}{joiner}timestamp={timestamp}&sign={sign}"
    return url, payload, {}


def build_wecom(channel: Channel, message: dict[str, Any], now: float) -> tuple[str, dict[str, Any], dict[str, str]]:
    lines = [f"**{message['title']}**"]
    if message.get("body"):
        lines.append(str(message["body"]))
    fields = _fields_lines(message)
    if fields:
        lines.append(fields)
    lines.append(f'<font color="comment">hookrelay · {message["source"]} · #{message["event_id"]}</font>')
    return channel.url, {"msgtype": "markdown", "markdown": {"content": "\n".join(lines)}}, {}


def build_generic(channel: Channel, message: dict[str, Any], now: float) -> tuple[str, dict[str, Any], dict[str, str]]:
    """The whole normalized event, signed the same way we verify inbound —
    a hookrelay can feed another hookrelay without ceremony."""
    headers: dict[str, str] = {}
    if channel.secret:
        body = json.dumps(message, ensure_ascii=False, sort_keys=True).encode()
        headers["X-Hook-Signature"] = hmac.new(channel.secret.encode(), body, hashlib.sha256).hexdigest()
    return channel.url, message, headers


_BUILDERS = {
    "feishu": build_feishu,
    "dingtalk": build_dingtalk,
    "wecom": build_wecom,
    "generic": build_generic,
}


def build_request(
    channel: Channel, message: dict[str, Any], now: float | None = None
) -> tuple[str, dict[str, Any], dict[str, str]]:
    builder = _BUILDERS[channel.type]
    return builder(channel, message, now if now is not None else time.time())


async def send(client: httpx.AsyncClient, channel: Channel, message: dict[str, Any]) -> tuple[bool, str]:
    """Deliver one message. Returns (ok, detail) — never raises: a delivery
    failure is a scheduling event for the caller, not an exception."""
    url, payload, headers = build_request(channel, message)
    try:
        response = await client.post(url, json=payload, headers=headers)
    except httpx.HTTPError as error:
        return False, f"transport: {error.__class__.__name__}: {error}"
    if response.status_code >= 300:
        return False, f"http {response.status_code}: {response.text[:200]}"
    # Feishu/DingTalk/WeCom answer 200 with an in-body error code — a 200 that
    # says "invalid sign" is still a failure, and pretending otherwise is how
    # dead webhooks stay invisible for weeks.
    try:
        data = response.json()
    except ValueError:
        return True, f"http {response.status_code}"
    for key in ("code", "errcode"):
        if isinstance(data, dict) and data.get(key) not in (None, 0):
            return False, f"remote {key}={data.get(key)}: {str(data.get('msg') or data.get('errmsg'))[:200]}"
    return True, f"http {response.status_code}"
