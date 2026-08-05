"""Channel adapters: pure request builders plus one thin sender.

Builders are registry entries — the four built-ins register through the same
decorator a plugin would use. A builder returns (url, payload, headers) and
touches no network; payload is either a dict (serialized by httpx) or BYTES.

Bytes matter when the payload is signed: the signature must cover the exact
octets that leave the socket. The first version of the generic builder signed
a sort_keys canonicalization while httpx serialized the dict its own way —
signature and wire bytes disagreed, and every downstream verification would
have failed. Signed builders now emit the final bytes themselves.
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

from hookrelay import registry
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

Payload = dict[str, Any] | bytes
BuiltRequest = tuple[str, Payload, dict[str, str]]


def _fields_lines(message: dict[str, Any]) -> str:
    fields = message.get("fields") or {}
    return "\n".join(f"**{name}**: {value}" for name, value in fields.items() if value)


@registry.channel("feishu")
def build_feishu(channel: Channel, message: dict[str, Any], now: float) -> BuiltRequest:
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


@registry.channel("dingtalk")
def build_dingtalk(channel: Channel, message: dict[str, Any], now: float) -> BuiltRequest:
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


@registry.channel("wecom")
def build_wecom(channel: Channel, message: dict[str, Any], now: float) -> BuiltRequest:
    lines = [f"**{message['title']}**"]
    if message.get("body"):
        lines.append(str(message["body"]))
    fields = _fields_lines(message)
    if fields:
        lines.append(fields)
    lines.append(f'<font color="comment">hookrelay · {message["source"]} · #{message["event_id"]}</font>')
    return channel.url, {"msgtype": "markdown", "markdown": {"content": "\n".join(lines)}}, {}


@registry.channel("generic")
def build_generic(channel: Channel, message: dict[str, Any], now: float) -> BuiltRequest:
    """The whole normalized event as canonical bytes, optionally signed.

    The signature covers EXACTLY the bytes returned, and the header NAME is
    configurable (signature_header) so hookrelay can speak a receiver's
    dialect — X-Webhook-Signature feeds WebhookWise's ingest directly.
    """
    body = json.dumps(message, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    headers: dict[str, str] = {"content-type": "application/json"}
    if channel.secret:
        headers[channel.signature_header] = hmac.new(channel.secret.encode(), body, hashlib.sha256).hexdigest()
    return channel.url, body, headers


def build_request(channel: Channel, message: dict[str, Any], now: float | None = None) -> BuiltRequest:
    builder = registry.CHANNEL_BUILDERS[channel.type]
    return builder(channel, message, now if now is not None else time.time())


async def send(client: httpx.AsyncClient, channel: Channel, message: dict[str, Any]) -> tuple[bool, str]:
    """Deliver one message. Returns (ok, detail) — never raises: a delivery
    failure is a scheduling event for the caller, not an exception."""
    url, payload, headers = build_request(channel, message)
    try:
        if isinstance(payload, bytes):
            response = await client.post(url, content=payload, headers=headers)
        else:
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
