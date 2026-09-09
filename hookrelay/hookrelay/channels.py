"""Channel adapters: pure request builders plus one thin sender.

Builders are registry entries — the two built-ins (`generic` for machines,
`bridge` for people, docs/bridge-protocol.md) register through the same
decorator a plugin would use; the markdown dialects for DingTalk and WeCom are
exactly such plugins (examples/plugins/chat_markdown_channels.py). No builder
here knows what any chat platform's message looks like. A builder returns (url, payload, headers) and
touches no network; payload is either a dict (serialized by httpx) or BYTES.

Bytes matter when the payload is signed: the signature must cover the exact
octets that leave the socket. The first version of the generic builder signed
a sort_keys canonicalization while httpx serialized the dict its own way —
signature and wire bytes disagreed, and every downstream verification would
have failed. Signed builders now emit the final bytes themselves.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

import httpx

from hookrelay import registry
from hookrelay.config import Channel
from hookrelay.extract import resolve_path
from hookrelay.processed import Processed

Payload = dict[str, Any] | bytes
# The version a bridge checks before reading anything else (docs/bridge-protocol.md).
BRIDGE_PROTOCOL = "hookstack-bridge/1"
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


# What the ledger's copy of a body must not keep. A custom bot's in-band `sign`
# was stored and served under a read guard that is open until a token is
# configured, so anyone who could reach the board could post into the group.
# The alert content is what answers a receiver's dispute; the signature is
# derived, reproducible, and nobody's evidence.
_SIGNING_KEYS = ("sign",)


def redact_for_ledger(body: bytes | None) -> str | None:
    """The bytes that left the socket, minus anything that authenticates them."""
    if body is None:
        return None
    text = body.decode("utf-8", "replace")
    try:
        parsed = json.loads(text)
    except ValueError:
        return text
    if not isinstance(parsed, dict) or not any(key in parsed for key in _SIGNING_KEYS):
        return text
    for key in _SIGNING_KEYS:
        if key in parsed:
            parsed[key] = "[redacted]"
    return json.dumps(parsed, ensure_ascii=False)


@registry.channel("generic")
def build_generic(channel: Channel, message: dict[str, Any], now: float) -> BuiltRequest:
    """Canonical JSON bytes, optionally signed — two contents:

    normalized (default): hookrelay's event summary.
    payload: raw — the ORIGINAL inbound payload, verbatim in content. This is
    what makes hookrelay a TRANSPARENT edge: point the url at your platform's
    ingest and the brain receives exactly what the monitoring system sent,
    unchanged, with hookrelay's ledger in between.

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


def card_model_for(channel: Channel, message: dict[str, Any]) -> dict[str, Any]:
    """The card as facts (docs/bridge-protocol.md), from whatever this channel
    was given: a brain's result under `payload: processed`, else the event
    itself. Plain text, unescaped — whoever renders it into a dialect escapes
    for that dialect. Shared with the markdown plugins in examples/plugins, so
    a dialect rendered in-process reads the same model a bridge does.
    """
    processed = _processed(channel, message)
    if processed is not None:
        return processed.card_model()
    if _prebuilt(channel, message) is not None:
        raise ValueError(
            f"channel {channel.name}: payload: raw is a finished platform payload; a card model is built here"
        )
    fields = message.get("fields") or {}
    card = {
        "title": str(message.get("title") or ""),
        "tone": str(message.get("level") or "info").lower(),
        "summary": str(message.get("body") or ""),
        "details": "\n".join(f"{name}: {value}" for name, value in fields.items() if value),
        "footer": f"hookrelay · {message.get('source')} · #{message.get('event_id')}",
    }
    return {key: value for key, value in card.items() if value}


@registry.channel("bridge", capabilities=("callbacks",))
def build_bridge(channel: Channel, message: dict[str, Any], now: float) -> BuiltRequest:
    """The chat-bridge protocol (docs/bridge-protocol.md): a card MODEL, signed
    the way this pipe signs everything it sends, to a sidecar that renders it in
    its platform's dialect and answers with the platform's message id.

    This is the builder that does not know what a Feishu card looks like. The
    three direct dialects above exist for custom-bot webhooks, which take a
    finished payload and can neither call back nor reply in a thread; a bridge
    can do both, and the pipe asks for both by FIELD (`reply_to`, `chat_id`),
    never by platform. The hints go in before signing, because the signature
    covers the exact bytes that leave — see the module docstring.
    """
    card = card_model_for(channel, message)
    envelope = _bridge_hints(channel, message, {"protocol": BRIDGE_PROTOCOL, "card": card})
    # For a bridge that delivers somewhere that cannot call back: where a link
    # for an action should land (this pipe's public address). The channel says;
    # nothing here knows what the bridge's far side is.
    link_base = str(channel.options.get("action_link_base") or "").strip().rstrip("/")
    if link_base:
        envelope["action_link_base"] = link_base
    body = json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    headers = {"content-type": "application/json"}
    if channel.secret:
        stamp = str(int(now))
        headers["X-Hook-Timestamp"] = stamp
        headers["X-Hook-Signature"] = hmac.new(
            channel.secret.encode(), stamp.encode() + b"." + body, hashlib.sha256
        ).hexdigest()
    return channel.url, body, headers


def build_request(channel: Channel, message: dict[str, Any], now: float | None = None) -> BuiltRequest:
    builder = registry.CHANNEL_BUILDERS[channel.type]
    return builder(channel, message, now if now is not None else time.time())


def _platform_message_id(data: Any) -> str:
    """The id the platform gave what we just sent, if it said. The bridge answers
    `{ok, message_id}`; Lark's own API answers `{code, data: {message_id}}`."""
    if not isinstance(data, dict):
        return ""
    direct = data.get("message_id")
    if isinstance(direct, str) and direct:
        return direct[:120]
    inner = data.get("data")
    if isinstance(inner, dict) and isinstance(inner.get("message_id"), str):
        return str(inner["message_id"])[:120]
    return ""


def _bridge_hints(channel: Channel, message: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Two keys only a bridge that sends as an application understands, added
    only when the channel says it is one. `reply_to` (with `options:
    {thread_replies: true}`) asks for this to be posted as a reply in the thread
    the event names in `fields.thread_root`; `chat_id` (with `options: {chat_id:
    …}`) names the chat, so one bridge can serve several channels. A custom-bot
    webhook has neither API and is never sent either key. The pipe carries the
    field values; it does not read them."""
    if channel.options.get("thread_replies"):
        root = str((message.get("fields") or {}).get("thread_root") or "").strip()
        if root:
            payload = {**payload, "reply_to": root[:120]}
    chat = str(channel.options.get("chat_id") or "").strip()
    if chat:
        payload = {**payload, "chat_id": chat[:120]}
    return payload


# What one request may ask about, matching what docs/bridge-protocol.md tells a
# bridge to answer. Asking for more is not an error, it is a silently short
# answer — so the caller slices here rather than discovering it at the far end.
READ_BATCH_MAX = 20


async def ask_read_status(
    client: httpx.AsyncClient, channel: Channel, message_ids: list[str], now: float
) -> dict[str, dict[str, Any]] | None:
    """Shape 4 of the bridge protocol: has anybody opened these? `None` = could
    not ask, which is not the same answer as "nobody has".

    That distinction is the whole feature. A card waiting three days is either a
    decision nobody has made or a card nobody has seen, and those have different
    fixes; a bridge that is down must not be reported as a room full of people
    ignoring you.
    """
    body = json.dumps(
        {"protocol": BRIDGE_PROTOCOL, "read": {"message_ids": list(message_ids[:READ_BATCH_MAX])}},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    headers = {"content-type": "application/json"}
    if channel.secret:
        stamp = str(int(now))
        headers["X-Hook-Timestamp"] = stamp
        headers["X-Hook-Signature"] = hmac.new(
            channel.secret.encode(), stamp.encode() + b"." + body, hashlib.sha256
        ).hexdigest()
    try:
        # Its own timeout, not the shared client's: a bridge answers this with
        # one platform call PER ID, so the request is slow by construction where
        # a card is one call. Measured: the first version reused the 10s
        # delivery timeout, asked about 59 cards at once, and the bridge died
        # mid-answer with a broken pipe — every card came back "unknown", which
        # is the honest classification of a question nobody finished asking.
        response = await client.post(channel.url, content=body, headers=headers, timeout=45.0)
        data = response.json() if response.status_code < 300 else None
    except (httpx.HTTPError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("ok"):
        return None
    # `supported: false` is a real answer — a webhook bridge never had an id to
    # ask about — and it is empty rather than unknown.
    read = data.get("read")
    return read if isinstance(read, dict) else {}


async def send(
    client: httpx.AsyncClient, channel: Channel, message: dict[str, Any]
) -> tuple[bool, str, bytes | None, str]:
    """Deliver one message. Returns (ok, detail, body, platform_message_id) —
    never raises: a delivery failure is a scheduling event for the caller, not
    an exception.

    `body` is the exact octets posted (None when the builder refused), so the
    ledger can keep what actually left the socket. The body only, never the
    headers — headers carry signatures and tokens. `platform_message_id` is what
    the platform called the message, "" when it did not say; it is the handle a
    reply in the card's thread will quote.
    """
    try:
        url, payload, headers = build_request(channel, message)
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        # A builder complaining about its inputs is a MISCONFIGURATION, and it
        # belongs in the delivery ledger with a name — not raised into the
        # worker, where one bad channel would stall every other delivery.
        # AttributeError is here too: a processed payload whose meta/analysis is
        # the wrong type reaches an accessor as a poison pill, and a row that
        # raises instead of failing is retried every tick forever.
        return False, f"build: {error.__class__.__name__}: {error}", None, ""
    if isinstance(payload, dict):
        payload = _bridge_hints(channel, message, payload)
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
    # How this receiver wants to be written to. Both are about the RECEIVER,
    # which is what a channel describes — not about the message, which is the
    # builder's. Writing a status back to a ticket system is the case that
    # needed them: the pipe could already point a `generic` channel at any URL,
    # but it could only ever POST, and it could not carry the API's own
    # credential. Restricted to the three methods that mean "here is a change":
    # GET is what the card-action page deliberately refuses to act on, and
    # DELETE from a config typo is not a mistake anybody recovers from.
    #
    # Config headers go UNDER the builder's, so a channel can add
    # `Authorization` and can never quietly replace a signature.
    extra = channel.options.get("headers")
    if isinstance(extra, dict):
        headers = {**{str(k): str(v) for k, v in extra.items()}, **headers}
    method = str(channel.options.get("method") or "POST").upper()
    try:
        response = await client.request(method, url, content=payload, headers=headers)
    except httpx.HTTPError as error:
        return False, f"transport: {error.__class__.__name__}: {error}", payload, ""
    if response.status_code >= 300:
        return False, f"http {response.status_code}: {response.text[:200]}", payload, ""
    # Feishu/DingTalk/WeCom answer 200 with an in-body error code — a 200 that
    # says "invalid sign" is still a failure, and pretending otherwise is how
    # dead webhooks stay invisible for weeks.
    try:
        data = response.json()
    except ValueError:
        return True, f"http {response.status_code}", payload, ""
    for key in ("code", "errcode"):
        if isinstance(data, dict) and data.get(key) not in (None, 0):
            return (
                False,
                f"remote {key}={data.get(key)}: {str(data.get('msg') or data.get('errmsg'))[:200]}",
                payload,
                "",
            )
    return True, f"http {response.status_code}", payload, _platform_message_id(data)
