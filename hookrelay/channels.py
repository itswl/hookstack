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
from hookrelay.extract import resolve_path

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


def _prebuilt(channel: Channel, message: dict[str, Any]) -> Any | None:
    """Raw mode: the upstream brain supplies the FINISHED payload; hookrelay
    owns delivery mechanics only (retry, rate limit, signing, the ledger) and
    keeps its hands off the content.

        options:
          payload: raw            # default: normalized
          payload_path: card      # optional sub-object of the inbound payload

    Returns None when the channel is in normalized mode. Raises ValueError
    when raw was requested but nothing is there — a misconfiguration must
    surface in the delivery ledger, not silently deliver an empty body."""
    if str(channel.options.get("payload") or "normalized") != "raw":
        return None
    selected = message.get("payload")
    path = channel.options.get("payload_path")
    if path:
        selected = resolve_path(selected, str(path))
    if selected is None:
        raise ValueError(f"payload: raw on channel {channel.name}: payload_path {path!r} yielded nothing")
    return selected


def _fields_lines(message: dict[str, Any]) -> str:
    fields = message.get("fields") or {}
    return "\n".join(f"**{name}**: {value}" for name, value in fields.items() if value)


def _feishu_sign_fields(secret: str, now: float) -> dict[str, str]:
    timestamp = str(int(now))
    key = f"{timestamp}\n{secret}".encode()
    sign = base64.b64encode(hmac.new(key, b"", hashlib.sha256).digest()).decode()
    return {"timestamp": timestamp, "sign": sign}


@registry.channel("feishu")
def build_feishu(channel: Channel, message: dict[str, Any], now: float) -> BuiltRequest:
    prebuilt = _prebuilt(channel, message)
    if prebuilt is not None:
        # The brain's finished Feishu message (interactive cards with their
        # callback buttons survive intact); only bot signing is injected here,
        # because the SENDER owns the timestamp.
        if not isinstance(prebuilt, dict):
            raise ValueError(f"channel {channel.name}: raw feishu payload must be an object")
        payload = dict(prebuilt)
        if channel.secret:
            payload.update(_feishu_sign_fields(channel.secret, now))
        return channel.url, payload, {}
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
        payload.update(_feishu_sign_fields(channel.secret, now))
    return channel.url, payload, {}


@registry.channel("dingtalk")
def build_dingtalk(channel: Channel, message: dict[str, Any], now: float) -> BuiltRequest:
    prebuilt = _prebuilt(channel, message)
    if prebuilt is not None:
        if not isinstance(prebuilt, dict):
            raise ValueError(f"channel {channel.name}: raw dingtalk payload must be an object")
        return _dingtalk_signed_url(channel, now), prebuilt, {}
    lines = [f"### {message['title']}"]
    if message.get("body"):
        lines.append(str(message["body"]))
    fields = _fields_lines(message)
    if fields:
        lines.append(fields)
    lines.append(f"> hookrelay · {message['source']} · #{message['event_id']}")
    payload = {"msgtype": "markdown", "markdown": {"title": message["title"], "text": "\n\n".join(lines)}}
    return _dingtalk_signed_url(channel, now), payload, {}


def _dingtalk_signed_url(channel: Channel, now: float) -> str:
    """DingTalk signing rides the query string: sign of "{ts_ms}\n{secret}"."""
    url = channel.url
    if channel.secret:
        timestamp = str(int(now * 1000))
        digest = hmac.new(channel.secret.encode(), f"{timestamp}\n{channel.secret}".encode(), hashlib.sha256).digest()
        sign = quote_plus(base64.b64encode(digest))
        joiner = "&" if "?" in url else "?"
        url = f"{url}{joiner}timestamp={timestamp}&sign={sign}"
    return url


@registry.channel("wecom")
def build_wecom(channel: Channel, message: dict[str, Any], now: float) -> BuiltRequest:
    prebuilt = _prebuilt(channel, message)
    if prebuilt is not None:
        if not isinstance(prebuilt, dict):
            raise ValueError(f"channel {channel.name}: raw wecom payload must be an object")
        return channel.url, prebuilt, {}
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
    """Canonical JSON bytes, optionally signed — two contents:

    normalized (default): hookrelay's event summary.
    payload: raw — the ORIGINAL inbound payload, verbatim in content. This is
    what makes hookrelay a TRANSPARENT edge: point the url at WebhookWise's
    /v1/webhook/{source} and the brain receives exactly what the monitoring
    system sent, unchanged, with hookrelay's ledger in between.

    The signature covers EXACTLY the bytes returned, and the header NAME is
    configurable (signature_header) so hookrelay can speak a receiver's
    dialect — X-Webhook-Signature feeds WebhookWise's ingest directly.
    """
    prebuilt = _prebuilt(channel, message)
    content: Any = (
        prebuilt
        if prebuilt is not None
        # The raw payload and any _-prefixed key are TRANSPORT, not content:
        # they must not enter the signed body a receiver verifies.
        else {key: value for key, value in message.items() if key != "payload" and not key.startswith("_")}
    )
    body = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    headers: dict[str, str] = {"content-type": "application/json"}
    if channel.secret:
        # Timestamped by default when the receiver speaks our own dialect, so
        # relay→relay hops are replay-protected; a foreign receiver (custom
        # signature_header) gets the body-only form it expects.
        if channel.signature_header == "X-Hook-Signature":
            stamp = str(int(now))
            headers["X-Hook-Timestamp"] = stamp
            signed = stamp.encode() + b"." + body
        else:
            signed = body
        headers[channel.signature_header] = hmac.new(channel.secret.encode(), signed, hashlib.sha256).hexdigest()
    return channel.url, body, headers


def build_request(channel: Channel, message: dict[str, Any], now: float | None = None) -> BuiltRequest:
    builder = registry.CHANNEL_BUILDERS[channel.type]
    return builder(channel, message, now if now is not None else time.time())


async def send(client: httpx.AsyncClient, channel: Channel, message: dict[str, Any]) -> tuple[bool, str]:
    """Deliver one message. Returns (ok, detail) — never raises: a delivery
    failure is a scheduling event for the caller, not an exception."""
    try:
        url, payload, headers = build_request(channel, message)
    except ValueError as error:
        return False, f"build: {error}"
    # Header, never body: a receiver that dedupes on it needs it stable across
    # retries, and it must not perturb the signature of the content.
    key = message.get("_idempotency_key")
    if key:
        headers = {**headers, "X-Hook-Idempotency-Key": str(key)}
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
