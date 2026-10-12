"""Inbound signature verification and the two access tokens.

Inbound: X-Hook-Signature = hex HMAC-SHA256 with the source's secret
("sha256=" prefix tolerated). With X-Hook-Timestamp the signature covers
"{timestamp}.{body}" and must be FRESH, which BOUNDS how long a captured
request stays useful rather than preventing replay outright; the door pairs it
with `replay_token` + `Store.claim_signature` to close that window (see
verify_signature). Without a timestamp the legacy body-only form is
accepted unless the door sets require_timestamp. A source configured without
a secret is accepted unsigned — a deliberate, documented decision for trusted
private networks, not a fallback.
"""

from __future__ import annotations

import hashlib
import hmac
import time


def constant_time_eq(expected: str, provided: str | None) -> bool:
    """Compare two header-derived strings without leaking length by timing.

    Wraps hmac.compare_digest because that function raises TypeError on a
    str holding non-ASCII, and Starlette decodes header bytes as latin-1 — so
    a single 0xF6 byte in any token or signature header turned every gate here
    into an unauthenticated HTTP 500 instead of a 401. Comparing the utf-8
    bytes keeps the constant-time property and answers the way it should.
    """
    return hmac.compare_digest(expected.encode("utf-8"), (provided or "").encode("utf-8"))


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
        seconds.
      body-only (legacy): no timestamp header, signature covers the body.
        Replayable forever, which is why require_timestamp exists per door:
        senders migrate first, then the door refuses the old form.

    A door with require_timestamp=True rejects the legacy form outright.

    WHAT THE TIMESTAMP ACTUALLY BUYS, stated exactly, because this used to claim
    it made a captured request "un-replayable" and that is more than it does:
    it BOUNDS the replay window to max_skew seconds. Inside that window the same
    signature is accepted again — a capture replayed within five minutes is
    indistinguishable from the original.

    CLOSED BY THE DOOR, not here (2026-10-12). A pure function has no store to
    write to, and that is deliberate: `verify_signature` is the one thing both
    the pipe's doors and any second implementation must agree on, so it stays
    pure. The door pairs it with `replay_token` and `Store.claim_signature`,
    which together answer the second copy as a duplicate before the pipeline and
    keep it from spending a second verdict. The old reasoning — "the window is
    narrower than the dedup window, so a replay lands as a duplicate anyway" —
    was true only up to the dedup window: an alert replayed at t=200s is inside
    the 300s skew, outside the 120s content window, and was a fresh judge call.

    The cache tracks the DEFAULT scheme's `X-Hook-Signature` and only that; a
    plugin adapter speaking `X-Hub-Signature-256` or Stripe's scheme is not
    tracked, which `replay_token` says out loud rather than guessing at a header
    it cannot own. The bridge's inbound door keeps its own skew check and no
    store, so its window stays bounded and `docs/containment.md` still says so.
    The card-action tokens in hookrelay/actions.py still get single use for their
    own reason: those spend money and restart services.
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
        return constant_time_eq(expected, provided)

    if require_timestamp:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return constant_time_eq(expected, provided)


def replay_token(source_name: str, signature: str | None) -> str | None:
    """One value identifying one signed request, or None if it cannot be one.

    Source-scoped so two sources that happen to share a secret cannot spend each
    other's rows, and normalised exactly the way `verify_signature` normalises
    (lowercased, `sha256=` prefix stripped) so the two agree about what "the same
    signature" means. Returns None when there is nothing worth tracking — no
    source name, or no signature header (a source with no secret, or a plugin
    scheme this does not know) — and a None means "let it through un-cached", not
    a cache miss. A guard that guessed at a plugin's header would either miss or
    refuse a legitimate retry, and neither is visible from here.

    One consequence worth stating, because it is the case a sender hits: a
    LEGACY body-only signature is a function of the body alone, so two identical
    bodies with no timestamp carry the SAME token. A legitimate identical re-send
    that lands between the content window (120s) and this cache's window (300s) is
    therefore folded where it used to be processed again. A TIMESTAMPED signature
    covers the timestamp, so a re-send there is a different token and is not
    folded — which is one more reason the timestamped form is the preferred one.
    """
    token = (signature or "").strip().lower().removeprefix("sha256=")
    if not source_name or not token:
        return None
    return f"{source_name}:{token}"


def token_ok(configured: str, presented: str | None) -> bool:
    """Empty configured token = the check is disabled (dev mode for read,
    endpoint disabled for admin — the CALLER decides which semantic applies)."""
    if not configured:
        return False
    return constant_time_eq(configured, presented)
