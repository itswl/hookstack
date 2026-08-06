"""Rendering primitives: dotted-path resolution and {brace} substitution.

A leaf module on purpose. Templates need these, config needs templates, and
extraction needs config — so the primitives must sit below all three or the
import graph forms a cycle (it did, once).
"""

from __future__ import annotations

import json
import re
from typing import Any

_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")


def resolve_path(data: Any, path: str) -> Any:
    """Walk dicts by key and lists by index; None for anything absent."""
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
    """Substitute {dotted.paths}. A missing path renders empty rather than
    raising: inbound payloads are other people's data, and a router that 500s
    on a surprise shape drops the message entirely — an empty title is
    recoverable, a lost event is not."""

    def _sub(match: re.Match[str]) -> str:
        # {a|b|c} takes the first path that yields something. Upstream shapes
        # carry the same meaning under different keys (eventArn here, region
        # there), and without fallbacks each variant needs its own template —
        # which is how one door ends up with a template per sender.
        for path in match.group(1).split("|"):
            value = resolve_path(payload, path.strip())
            if value is None or value == "":
                continue
            if isinstance(value, (dict, list)):
                return json.dumps(value, ensure_ascii=False)
            return str(value)
        return ""

    return _PLACEHOLDER.sub(_sub, template).strip()
