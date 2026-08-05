"""Inbound signature verification and the two access tokens.

Inbound: X-Hook-Signature = hex HMAC-SHA256 of the raw body with the source's
secret ("sha256=" prefix tolerated). A source configured without a secret is
accepted unsigned — that is a deliberate, documented decision for trusted
private networks, not a fallback.
"""

from __future__ import annotations

import hashlib
import hmac


def verify_signature(secret: str, body: bytes, header_value: str | None) -> bool:
    if not secret:
        return True
    if not header_value:
        return False
    provided = header_value.strip()
    provided = provided.removeprefix("sha256=")
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, provided.lower())


def token_ok(configured: str, presented: str | None) -> bool:
    """Empty configured token = the check is disabled (dev mode for read,
    endpoint disabled for admin — the CALLER decides which semantic applies)."""
    if not configured:
        return False
    return hmac.compare_digest(configured, presented or "")
