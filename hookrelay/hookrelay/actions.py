"""Card actions: the signed token that lets a button press mean something.

A notification card used to be a dead end. Everything an operator wants to do
on reading one — quiet this alert, ask the investigator a question, approve the
fix it proposed — lived behind a web board and a bearer token, which is not
where the human is at 3am. The card is where they are.

WHO SIGNS. The processed-event contract's first draft said actions arrive
"pre-signed by the brain", on the reasoning that a signature is judgement about
identity rather than formatting. That was rewritten here, and the reason is
worth keeping: signing needs a secret, three brains signing means the secret
lives in three places, and the same HMAC comparison written three times is
exactly how one non-ASCII byte became an unauthenticated 500 in five files at
once (2026-08-19). So the split moved one notch:

    the brain declares WHICH actions its verdict deserves   — judgement
    the pipe mints, carries and verifies the token          — the channel edge

A brain sends `{"kind": "silence", "text": "Silence 1h", "minutes": 60}` and
holds no secret at all. The pipe turns that into an opaque token, renders it as
a button value, and is the only component that can read one back.

WHAT THE TOKEN IS. `base64url(payload) + "." + hex HMAC-SHA256`, carrying the
kind, the event it belongs to, its params and an expiry. It is a bearer
credential for one action on one alert, so it is bounded twice over: a short
TTL, and single use (the caller records spent tokens — see Store.spend_action).
That matters most for the actions that cost money: a card forwarded into a
group chat is a token in everyone's scrollback.

FAIL CLOSED. A kind the pipe has not been configured to accept is dropped
before minting. A brain asking for `approve` against a deployment that never
enabled it gets no button, not an unusable one.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any

from hookrelay.security import constant_time_eq

# Long enough that a card read after a meeting still works, short enough that a
# token left in a group chat's scrollback stops being a key. Overridable per
# deployment; the default is the one an operator would pick.
DEFAULT_TTL_SECONDS = 24 * 3600

# Every kind the pipe knows how to carry. A brain may declare any of them; the
# deployment's config decides which are actually enabled.
KINDS = ("silence", "followup", "approve", "useful", "useless", "remember")


class ActionError(ValueError):
    """A token that cannot be trusted. The reason is for the log, not the caller."""


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def mint(
    secret: str,
    *,
    kind: str,
    event_id: int,
    correlation_id: str,
    params: dict[str, Any] | None = None,
    now: float,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> str:
    """One token for one action on one alert.

    `jti` is what makes single use enforceable: the caller stores it on spend,
    so a second press of the same button is refused by identity rather than by
    guessing from timestamps.
    """
    if kind not in KINDS:
        raise ActionError(f"unknown action kind {kind!r}")
    payload = {
        "k": kind,
        "e": int(event_id),
        "c": str(correlation_id or ""),
        "p": params or {},
        "x": int(now) + max(1, ttl_seconds),
        # Short random-free identity: the token body is already unguessable
        # because it is signed, and a digest of it keeps mint() pure.
        "j": hashlib.sha256(f"{kind}|{event_id}|{correlation_id}|{now!r}|{params!r}".encode()).hexdigest()[:16],
    }
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    encoded = _b64(body)
    signature = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}"


def verify(secret: str, token: str, *, now: float) -> dict[str, Any]:
    """The token's claims, or ActionError. Never returns a partial answer.

    Signature before parse, and expiry before use: a token whose signature does
    not check out has told us nothing, so nothing inside it is worth reading.
    """
    if not secret:
        raise ActionError("no action secret configured — the callback door is closed")
    if not token or "." not in token:
        raise ActionError("malformed token")
    encoded, _, signature = token.rpartition(".")
    expected = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
    if not constant_time_eq(expected, signature):
        raise ActionError("bad signature")
    try:
        claims = json.loads(_unb64(encoded))
    except (ValueError, TypeError) as exc:
        raise ActionError("token body is not JSON") from exc
    if not isinstance(claims, dict):
        raise ActionError("token body is not an object")
    if str(claims.get("k")) not in KINDS:
        raise ActionError(f"unknown action kind {claims.get('k')!r}")
    if float(claims.get("x") or 0) < now:
        raise ActionError("token expired")
    return claims


def offered(
    secret: str,
    declared: list[dict[str, Any]],
    enabled: dict[str, dict[str, Any]],
    *,
    event_id: int,
    correlation_id: str,
    now: float,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> list[dict[str, Any]]:
    """Turn a brain's declarations into rendered, signed buttons.

    Drops silently rather than raising: a brain asking for an action this
    deployment never enabled is a difference of opinion about what is on offer,
    not a delivery failure — and a verdict must still reach the channel.
    """
    if not secret:
        return []
    buttons: list[dict[str, Any]] = []
    for item in declared:
        kind = str(item.get("kind") or "").strip().lower()
        if kind not in enabled:
            continue
        # The brain's extra keys are the params; text and style are presentation
        # and never travel inside the token.
        params = {k: v for k, v in item.items() if k not in ("kind", "text", "style")}
        params.update(enabled[kind].get("params") or {})
        buttons.append(
            {
                "text": str(item.get("text") or kind.title()),
                "style": str(item.get("style") or "default"),
                "value": {
                    "hookrelay_action": mint(
                        secret,
                        kind=kind,
                        event_id=event_id,
                        correlation_id=correlation_id,
                        params=params,
                        now=now,
                        ttl_seconds=ttl_seconds,
                    )
                },
            }
        )
    return buttons
