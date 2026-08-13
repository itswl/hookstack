"""The PROCESSED-EVENT contract: what a brain hands back for the pipe to dress.

The division of labour this encodes: a brain judges, the pipe formats. A brain
that renders Feishu cards has to know Feishu's card schema, its colour names,
its markdown dialect — and then know WeCom's, and DingTalk's. That is the dirty
work being lifted out of it.

So a brain sends its RESULT, shaped like this (every key optional but `analysis`
and `meta.alert_name` in practice):

    {
      "meta":     {"alert_name", "source", "importance", "brain", "rule_name",
                   "correlation_id", "is_recovery", "is_periodic_reminder",
                   "event_id", "timestamp"},
      "analysis": {"summary", "event_type", "impact_scope", "confidence"},
      "identity": {"project": "...", "env": "prod", ...},   # meaningful values
      "links":    [{"text": "...", "url": "..."}],
      "actions":  [{"text": "Acknowledge", "value": {...}}]   # pre-signed by the brain
    }

and every channel type renders it its own way. `actions` are carried but only
by channels that HAVE interactive callbacks (Feishu): the value is opaque and
already signed by the brain, because a signature is judgement about identity,
not formatting.

Rendering lives here rather than in each builder so the five blocks stay in one
place: headline, identity breadcrumb, impact, links, footer.
"""

from __future__ import annotations

import time
from typing import Any

# Header colour by importance. Green whenever the alert has ENDED, whatever its
# importance was: a recovery card wearing a red header contradicts its own text.
_FEISHU_COLOR = {"critical": "red", "high": "red", "warning": "orange", "medium": "orange", "low": "wathet"}
_LEVEL_TAG = {
    "critical": "🔴 CRITICAL",
    "high": "🔴 HIGH",
    "warning": "🟠 MEDIUM",
    "medium": "🟠 MEDIUM",
    "low": "🔵 LOW",
}


class Processed:
    """A brain's result, with the accessors every renderer needs."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.raw = payload if isinstance(payload, dict) else {}
        self.meta: dict[str, Any] = self.raw.get("meta") or {}
        self.analysis: dict[str, Any] = self.raw.get("analysis") or {}
        self.identity: dict[str, Any] = self.raw.get("identity") or {}
        self.links: list[dict[str, Any]] = [x for x in (self.raw.get("links") or []) if isinstance(x, dict)]
        self.actions: list[dict[str, Any]] = [x for x in (self.raw.get("actions") or []) if isinstance(x, dict)]

    @property
    def importance(self) -> str:
        return str(self.meta.get("importance") or self.analysis.get("importance") or "medium").lower()

    @property
    def is_recovery(self) -> bool:
        return bool(self.meta.get("is_recovery"))

    @property
    def title(self) -> str:
        return str(self.meta.get("alert_name") or self.analysis.get("summary") or "Alert")

    @property
    def summary(self) -> str:
        return str(self.analysis.get("summary") or "")

    @property
    def headline(self) -> str:
        """Header text: state first, because "did it end?" outranks "how bad"."""
        if self.is_recovery:
            return f"✅ Resolved · {self.title}"
        if self.meta.get("is_periodic_reminder"):
            return f"🔁 Still open · {self.title}"
        return f"📡 {self.title}"

    @property
    def color(self) -> str:
        return "green" if self.is_recovery else _FEISHU_COLOR.get(self.importance, "turquoise")

    @property
    def level_tag(self) -> str:
        return _LEVEL_TAG.get(self.importance, self.importance)

    def breadcrumb(self) -> str:
        """Identity as one readable line, not a label grid (which read cluttered)."""
        return " · ".join(f"{value}" for value in self.identity.values() if str(value).strip())

    @staticmethod
    def _stamp(value: Any) -> str:
        """A brain sends an epoch; a person reads a clock.

        meta.timestamp went into the card as the float it arrived as, so every
        notification ended "· 1786037727.669673". Same format as the status
        page (MM-DD HH:MM:SS, deployment-local) so one alert reads the same in
        both places. A brain that already formatted its own string keeps it.
        """
        if value is None or (isinstance(value, str) and not value.strip()):
            return ""
        try:
            epoch = float(value)
        except (TypeError, ValueError):
            return str(value).strip()
        # Milliseconds are common enough to be worth absorbing rather than
        # rendering as a date in the year 58000.
        if epoch > 1e11:
            epoch /= 1000.0
        try:
            return time.strftime("%m-%d %H:%M:%S", time.localtime(epoch))
        except (OverflowError, OSError, ValueError):
            return ""

    def footer(self) -> str:
        bits = [
            str(self.meta.get("source") or ""),
            str(self.analysis.get("event_type") or ""),
            self._stamp(self.meta.get("timestamp")),
        ]
        return " · ".join(b for b in bits if b)

    # ── per-vendor rendering ─────────────────────────────────────────────

    def feishu_card(self) -> dict[str, Any]:
        elements: list[dict[str, Any]] = []
        # 1) headline: how bad + what happened, the first thing the eye lands on.
        lead = f"**{self.level_tag}**  {self.summary}" if self.summary else f"**{self.level_tag}**"
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": lead}})
        # 2) identity breadcrumb
        crumb = self.breadcrumb()
        if crumb:
            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": crumb}})
        # 3) impact, titled so it reads as secondary to the headline
        impact = str(self.analysis.get("impact_scope") or "")
        if impact:
            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"**Impact**\n{impact}"}})
        # 4) runbooks at notification time, not after a dashboard visit
        if self.links:
            lines = "\n".join(f"[{link.get('text') or link.get('url')}]({link.get('url')})" for link in self.links)
            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"**Runbooks**\n{lines}"}})
        # 5) interactive actions — only Feishu has callbacks; values are opaque
        #    and already signed by the brain.
        if self.actions:
            elements.append(
                {
                    "tag": "action",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": str(a.get("text") or "Action")},
                            "type": str(a.get("style") or "default"),
                            "value": a.get("value") or {},
                        }
                        for a in self.actions
                    ],
                }
            )
        # 6) metadata footer, de-emphasised so it does not compete with content
        footer = self.footer()
        if footer:
            elements.append({"tag": "note", "elements": [{"tag": "plain_text", "content": footer}]})
        return {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": self.headline},
                    "template": self.color,
                },
                "elements": elements,
            },
        }

    def markdown(self, *, heading: bool) -> str:
        """DingTalk wants a `### heading`; WeCom renders bold instead."""
        lines = [f"### {self.headline}" if heading else f"**{self.headline}**"]
        if self.summary:
            lines.append(f"{self.level_tag} {self.summary}")
        crumb = self.breadcrumb()
        if crumb:
            lines.append(crumb)
        impact = str(self.analysis.get("impact_scope") or "")
        if impact:
            lines.append(f"**Impact**: {impact}")
        for link in self.links:
            lines.append(f"[{link.get('text') or link.get('url')}]({link.get('url')})")
        # No buttons: these bots have no callback channel, and a button that
        # does nothing is worse than no button. The links still travel.
        footer = self.footer()
        if footer:
            lines.append(f"> {footer}")
        return "\n\n".join(lines)
