"""Source adapters: how an upstream authenticates and what its payload means.

An adapter answers two questions the door must ask:
    verify(source, body, headers) — is this really them?
    parse(source, payload)        — what are they saying? (→ title/body/level/fields)

The default adapter is the original behaviour: X-Hook-Signature HMAC-SHA256
plus {dotted.path} template extraction. A plugin adapter can speak GitHub's
X-Hub-Signature-256, GitLab's token header, Stripe's scheme — anything —
without the core knowing those services exist.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from hookrelay import registry
from hookrelay.config import Source
from hookrelay.extract import extract_event
from hookrelay.security import verify_signature


@registry.source_adapter("default")
class DefaultAdapter:
    def verify(self, source: Source, body: bytes, headers: Mapping[str, str]) -> bool:
        return verify_signature(
            source.secret,
            body,
            headers.get("x-hook-signature"),
            timestamp_value=headers.get("x-hook-timestamp"),
            max_skew_seconds=source.max_skew_seconds,
            require_timestamp=source.require_timestamp,
        )

    def parse(self, source: Source, payload: Any) -> dict[str, Any]:
        return extract_event(source, payload)
