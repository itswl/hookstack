"""Field extraction: dotted paths inside {braces}, and nothing more.

"{alerts.0.labels.alertname}" walks dicts by key and lists by index. A missing
segment renders as an empty string rather than raising: inbound payloads are
other people's data, and a router that 500s on a surprise shape drops the
message entirely — an empty title is recoverable, a lost event is not.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from hookrelay.config import Source

_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")


def resolve_path(data: Any, path: str) -> Any:
    current = data
    for token in path.split("."):
        if isinstance(current, list):
            try:
                current = current[int(token)]
            except (ValueError, IndexError):
                return None
        elif isinstance(current, dict):
            if token not in current:
                return None
            current = current[token]
        else:
            return None
    return current


def render(template: str, payload: Any) -> str:
    def _sub(match: re.Match[str]) -> str:
        value = resolve_path(payload, match.group(1).strip())
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return str(value)

    return _PLACEHOLDER.sub(_sub, template).strip()


def extract_event(source: Source, payload: Any) -> dict[str, Any]:
    """Normalize an inbound payload to the shape every channel understands."""
    level_raw = render(source.level, payload).lower() if source.level else ""
    level = source.level_map.get(level_raw, level_raw) or "info"
    return {
        "title": render(source.title, payload) or f"webhook from {source.name}",
        "body": render(source.body, payload),
        "level": level,
        "fields": {name: render(template, payload) for name, template in source.fields.items()},
    }


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
