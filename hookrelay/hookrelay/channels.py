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
from hookrelay.processed import Processed

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


def _processed(channel: Channel, message: dict[str, Any]) -> Processed | None:
    """`payload: processed` — the brain judged, the pipe dresses.

    The brain sends a RESULT (see hookrelay/processed.py) and each channel type
    renders it in its own dialect: a Feishu card, DingTalk markdown, WeCom
    markdown, or the structure itself for a generic receiver. This is the
    division that lets a brain stop knowing what a Feishu card looks like.
    """
    if str(channel.options.get("payload") or "normalized") != "processed":
        return None
    payload = message.get("payload")
    if not isinstance(payload, dict):
        raise TypeError(f"payload: processed on channel {channel.name}: inbound payload is not an object")
    return Processed(payload)


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
    processed = _processed(channel, message)
    if processed is not None:
        payload = processed.feishu_card()
        if channel.secret:
            payload.update(_feishu_sign_fields(channel.secret, now))
        return channel.url, payload, {}
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
    payload = {
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
    processed = _processed(channel, message)
    if processed is not None:
        payload = {
            "msgtype": "markdown",
            "markdown": {"title": processed.headline, "text": processed.markdown(heading=True)},
        }
        return _dingtalk_signed_url(channel, now), payload, {}
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
    processed = _processed(channel, message)
    if processed is not None:
        content = {"msgtype": "markdown", "markdown": {"content": processed.markdown(heading=False)}}
        return channel.url, content, {}
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
    what makes hookrelay a TRANSPARENT edge: point the url at your platform's
    ingest and the brain receives exactly what the monitoring system sent, unchanged, with hookrelay's ledger in between.

    The signature covers EXACTLY the bytes returned, and the header NAME is
    configurable (signature_header) so hookrelay can speak a receiver's
    dialect — X-Webhook-Signature for a receiver that expects that name.
    """
    processed = _processed(channel, message)
    if processed is not None:
        body = json.dumps(processed.raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        headers: dict[str, str] = {"content-type": "application/json"}
        if channel.secret:
            headers[channel.signature_header] = hmac.new(channel.secret.encode(), body, hashlib.sha256).hexdigest()
        return channel.url, body, headers

    prebuilt = _prebuilt(channel, message)
    content: Any = (
        prebuilt
        if prebuilt is not None
        # The raw payload and any _-prefixed key are TRANSPORT, not content:
        # they must not enter the signed body a receiver verifies.
        else {key: value for key, value in message.items() if key != "payload" and not key.startswith("_")}
    )
    body = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    headers = {"content-type": "application/json"}
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


async def send(client: httpx.AsyncClient, channel: Channel, message: dict[str, Any]) -> tuple[bool, str, bytes | None]:
    """Deliver one message. Returns (ok, detail, body) — never raises: a
    delivery failure is a scheduling event for the caller, not an exception.

    `body` is the exact octets posted (None when the builder refused), so the
    ledger can keep what actually left the socket. The body only, never the
    headers — headers carry signatures and tokens.
    """
    try:
        url, payload, headers = build_request(channel, message)
    except (ValueError, TypeError, KeyError) as error:
        # A builder complaining about its inputs is a MISCONFIGURATION, and it
        # belongs in the delivery ledger with a name — not raised into the
        # worker, where one bad channel would stall every other delivery.
        return False, f"build: {error.__class__.__name__}: {error}", None
    # Headers, never body: a receiver that dedupes needs a stable key, and a
    # brain that will hand work BACK to us needs something to quote so the two
    # halves of a round trip can be found together. Neither may perturb the
    # signature of the content.
    key = message.get("_idempotency_key")
    if key:
        headers = {**headers, "X-Hook-Idempotency-Key": str(key)}
    correlation = message.get("_correlation_id")
    if correlation:
        headers = {
            **headers,
            "X-Hook-Correlation-Id": str(correlation),
            # X-Request-Id too: it is what allowlist-minded receivers (ours
            # included) actually keep, so the id survives to be echoed back.
            "X-Request-Id": str(correlation),
        }
    if not isinstance(payload, bytes):
        # One serialization for the wire AND the ledger: the copy the ledger
        # keeps is byte-identical to the copy that was sent.
        payload = json.dumps(payload, ensure_ascii=False).encode()
        if "content-type" not in {key.lower() for key in headers}:
            headers = {**headers, "content-type": "application/json"}
    try:
        response = await client.post(url, content=payload, headers=headers)
    except httpx.HTTPError as error:
        return False, f"transport: {error.__class__.__name__}: {error}", payload
    if response.status_code >= 300:
        return False, f"http {response.status_code}: {response.text[:200]}", payload
    # Feishu/DingTalk/WeCom answer 200 with an in-body error code — a 200 that
    # says "invalid sign" is still a failure, and pretending otherwise is how
    # dead webhooks stay invisible for weeks.
    try:
        data = response.json()
    except ValueError:
        return True, f"http {response.status_code}", payload
    for key in ("code", "errcode"):
        if isinstance(data, dict) and data.get(key) not in (None, 0):
            return (
                False,
                f"remote {key}={data.get(key)}: {str(data.get('msg') or data.get('errmsg'))[:200]}",
                payload,
            )
    return True, f"http {response.status_code}", payload
