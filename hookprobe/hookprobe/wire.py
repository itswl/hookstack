"""The family's wire dialect: timestamped HMAC signatures, both directions.

hookrelay signs generic-channel deliveries as X-Hook-Signature =
hex HMAC-SHA256(secret, "{timestamp}.{body}") with X-Hook-Timestamp, and its
own front doors verify the same form. hookprobe speaks it on both sides: the
event door verifies what the pipe sends, the return delivery signs what goes
back. An empty secret means unsigned — a deliberate decision for a private
network, never a default to drift into.
"""

from __future__ import annotations

import hashlib
import hmac
import time


def sign_timestamped(secret: str, body: bytes, *, now: float | None = None) -> dict[str, str]:
    """Headers for an outbound family delivery; empty when unsigned."""
    if not secret:
        return {}
    stamp = str(int(time.time() if now is None else now))
    digest = hmac.new(secret.encode(), stamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    return {"X-Hook-Timestamp": stamp, "X-Hook-Signature": digest}


def verify_timestamped(
    secret: str,
    body: bytes,
    signature: str | None,
    timestamp: str | None,
    *,
    now: float | None = None,
    max_skew_seconds: int = 300,
) -> bool:
    """Verify the pipe's signature over "{timestamp}.{body}", with freshness."""
    if not secret:
        return True
    if not signature or not timestamp:
        return False
    try:
        sent_at = float(timestamp.strip())
    except ValueError:
        return False
    current = time.time() if now is None else now
    if abs(current - sent_at) > max(1, max_skew_seconds):
        return False
    provided = signature.strip().removeprefix("sha256=").lower()
    expected = hmac.new(secret.encode(), timestamp.strip().encode() + b"." + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, provided)
