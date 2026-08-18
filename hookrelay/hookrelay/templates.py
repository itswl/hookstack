"""Extraction templates: how ONE door reads MANY payload shapes.

A door often faces more than one sender. The production `inbound` door takes
Grafana alerts and SNS relays through the same public URL, and with a single
template the shapes it does not understand fall back to "webhook from
inbound" — an event in the ledger that cannot be identified, which is the same
as a lost event for anyone trying to answer a question with it.

So a door carries an ORDERED list of named templates, each with an optional
selector evaluated against the RAW payload (never against extracted fields —
that would make selection depend on its own output). First match wins; a
template with no selector is a fallback and belongs last.

Which template matched is recorded in the decision trace. "Why is this title
empty" must be answerable from the ledger, not by re-deriving the payload.

Render templates (field dict → wire format) are a different KIND and are not
here yet: in a brain-paired deployment both ends run raw, so nothing would
consume them. The `kind` field exists so adding them later is additive.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from hookrelay.render import render, resolve_path


@dataclass(frozen=True, slots=True)
class TemplateSelector:
    """Selector over the raw payload. Empty = always matches (fallback).

    match:
      exists: [evalMatches, alerts]     # every path must be present
      equals: {state: alerting}         # path must render to this value
    """

    exists: tuple[str, ...] = ()
    equals: dict[str, str] = field(default_factory=dict)
    # any_of: at least ONE of these paths must be present. Real senders vary
    # within an ecosystem (an alert list here, a single alert there), and
    # requiring all of them would need a template per variant.
    any_of: tuple[str, ...] = ()

    @property
    def is_fallback(self) -> bool:
        return not self.exists and not self.equals and not self.any_of

    def matches(self, payload: Any) -> bool:
        for path in self.exists:
            if resolve_path(payload, path) is None:
                return False
        for path, expected in self.equals.items():
            value = resolve_path(payload, path)
            if value is None or str(value) != str(expected):
                return False
        return not (self.any_of and all(resolve_path(payload, path) is None for path in self.any_of))


@dataclass(frozen=True, slots=True)
class ExtractTemplate:
    name: str
    title: str = "{title}"
    body: str = "{body}"
    level: str = ""
    level_map: dict[str, str] = field(default_factory=dict)
    fields: dict[str, str] = field(default_factory=dict)
    # Optional recovery-flag template. Rendered and truth-tested into a
    # top-level `is_recovery` on the event — NOT a field, because anything
    # that differs between a firing and its recovery would split their
    # identity and the recovery could never find its firing. A platform that
    # states the fact (e.g. WebhookWise's meta.is_recovery) beats keyword
    # sniffing downstream.
    recovery: str = ""
    selector: TemplateSelector = TemplateSelector()

    def extract(self, payload: Any, *, door: str) -> dict[str, Any]:
        level_raw = render(self.level, payload).lower() if self.level else ""
        level = self.level_map.get(level_raw, level_raw) or "info"
        event: dict[str, Any] = {
            "title": render(self.title, payload) or f"webhook from {door}",
            "body": render(self.body, payload),
            "level": level,
            "fields": {name: render(tpl, payload) for name, tpl in self.fields.items()},
            "_template": self.name,
        }
        if self.recovery:
            flag = render(self.recovery, payload).strip().lower()
            event["is_recovery"] = flag in ("true", "1", "yes", "resolved", "recovery", "recovered")
        return event


def select(templates: tuple[ExtractTemplate, ...], payload: Any) -> ExtractTemplate:
    """First template whose selector matches; the last one is the safety net.

    A door always has at least one template (config guarantees it), so this
    never fails — an unreadable payload becomes a poorly-titled event, never a
    dropped one.
    """
    for template in templates:
        if template.selector.matches(payload):
            return template
    return templates[-1]
