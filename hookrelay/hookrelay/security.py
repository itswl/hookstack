"""Inbound signature verification and the two access tokens.

Inbound: X-Hook-Signature = hex HMAC-SHA256 with the source's secret
("sha256=" prefix tolerated). With X-Hook-Timestamp the signature covers
"{timestamp}.{body}" and must be FRESH, which is what stops a captured
request from being replayed forever; without it the legacy body-only form is
accepted unless the door sets require_timestamp. A source configured without
a secret is accepted unsigned — a deliberate, documented decision for trusted
private networks, not a fallback.
"""

from __future__ import annotations

import hashlib
import hmac
import time


def verify_signature(
    secret: str,
    body: bytes,
    header_value: str | None,
    *,
    timestamp_value: str | None = None,
    now: float | None = None,
    max_skew_seconds: int = 300,
    require_timestamp: bool = False,
) -> bool:
    """Verify X-Hook-Signature over the raw body, optionally with freshness.

    Two accepted forms, because a signing scheme must be able to change
    without a flag day:

      timestamped (preferred): X-Hook-Timestamp present, signature covers
        "{timestamp}.{body}", and the timestamp must be within max_skew
        seconds. This is what makes a captured request un-replayable.
      body-only (legacy): no timestamp header, signature covers the body.
        Replayable forever, which is why require_timestamp exists per door:
        senders migrate first, then the door refuses the old form.

    A door with require_timestamp=True rejects the legacy form outright.
    """
    if not secret:
        return True
    if not header_value:
        return False
    provided = header_value.strip().removeprefix("sha256=").lower()

    if timestamp_value:
        try:
            sent_at = float(str(timestamp_value).strip())
        except ValueError:
            return False
        current = time.time() if now is None else now
        if abs(current - sent_at) > max(1, max_skew_seconds):
            return False
        signed = str(timestamp_value).strip().encode() + b"." + body
        expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, provided)

    if require_timestamp:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, provided)


def token_ok(configured: str, presented: str | None) -> bool:
    """Empty configured token = the check is disabled (dev mode for read,
    endpoint disabled for admin — the CALLER decides which semantic applies)."""
    if not configured:
        return False
    return hmac.compare_digest(configured, presented or "")
