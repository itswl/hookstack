"""Field extraction: dotted paths inside {braces}, and nothing more.

"{alerts.0.labels.alertname}" walks dicts by key and lists by index. A missing
segment renders as an empty string rather than raising: inbound payloads are
other people's data, and a router that 500s on a surprise shape drops the
message entirely — an empty title is recoverable, a lost event is not.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from hookrelay.config import Source

# Re-exported: the primitives live in the leaf module now (import cycle), but
# channels/plugins have imported them from here since day one.
from hookrelay.render import render, resolve_path  # noqa: F401


def extract_event(source: Source, payload: Any) -> dict[str, Any]:
    """Normalize an inbound payload, using the first template that claims it.

    The result carries `_template` (the name that matched) so the decision
    trace can say WHICH reading produced this title — "why is the title empty"
    must be answerable from the ledger.
    """
    from hookrelay.templates import select

    if not source.templates:  # defensive: config always fills this
        level_raw = render(source.level, payload).lower() if source.level else ""
        level = source.level_map.get(level_raw, level_raw) or "info"
        return {
            "title": render(source.title, payload) or f"webhook from {source.name}",
            "body": render(source.body, payload),
            "level": level,
            "fields": {name: render(tpl, payload) for name, tpl in source.fields.items()},
            "_template": "inline",
        }
    return select(source.templates, payload).extract(payload, door=source.name)


def fingerprint(source: Source, extracted: dict[str, Any]) -> str:
    """Duplicate identity: configured fields, or title+body when unconfigured.

    Computed over EXTRACTED values, not the raw payload, so upstream noise in
    ignored fields (timestamps, sequence numbers) does not defeat dedup.
    """
    if source.fingerprint_fields:
        basis: dict[str, Any] = {}
        for name in source.fingerprint_fields:
            if name in ("title", "body", "level"):
                basis[name] = extracted.get(name, "")
            else:
                basis[name] = extracted.get("fields", {}).get(name, "")
    else:
        basis = {"title": extracted.get("title", ""), "body": extracted.get("body", "")}
    canonical = json.dumps({"source": source.name, **basis}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
