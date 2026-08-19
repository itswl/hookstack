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

from hookrelay.markup import escape_markup, markdown_link

# Feishu card header colour by normalized level/importance. Red is reserved for
# high — the family colour doctrine: alarm colours are earned, not decorative.
# Green whenever the alert has ENDED, whatever its importance was: a recovery
# card wearing a red header contradicts its own text.
#
# ONE table, exported. channels.py kept a second copy that disagreed about
# `info`, so the same alert wore a different header depending on which of the two
# paths rendered it — and a colour that means something different per code path
# means nothing at all.
FEISHU_LEVEL_COLOR = {
    "critical": "red",
    "high": "red",
    "warning": "orange",
    "medium": "orange",
    "low": "wathet",
    "info": "blue",
}
# Anything the vocabulary does not name: readable, and visibly not an alarm.
FEISHU_FALLBACK_COLOR = "turquoise"

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
        return "green" if self.is_recovery else FEISHU_LEVEL_COLOR.get(self.importance, FEISHU_FALLBACK_COLOR)

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
        # Every block below is a `lark_md` element, so every piece of the brain's
        # text is escaped on the way in — see hookrelay/markup.py for what one
        # unescaped alert title did to a company's phones. The header title and
        # the footer note are `plain_text`, which renders no markup, and stay
        # verbatim: escaping them would only show operators our backslashes.
        # 1) headline: how bad + what happened, the first thing the eye lands on.
        tag = escape_markup(self.level_tag)
        summary = escape_markup(self.summary)
        lead = f"**{tag}**  {summary}" if summary else f"**{tag}**"
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": lead}})
        # 2) identity breadcrumb
        crumb = self.breadcrumb()
        if crumb:
            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": escape_markup(crumb)}})
        # 3) impact, titled so it reads as secondary to the headline
        impact = str(self.analysis.get("impact_scope") or "")
        if impact:
            content = f"**Impact**\n{escape_markup(impact)}"
            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": content}})
        # 4) runbooks at notification time, not after a dashboard visit
        if self.links:
            lines = "\n".join(rendered for rendered in map(self._link, self.links) if rendered)
            if lines:
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

    @staticmethod
    def _link(link: dict[str, Any]) -> str:
        """One `links` entry, rendered only as far as its url earns."""
        return markdown_link(str(link.get("text") or ""), str(link.get("url") or ""))

    def markdown(self, *, heading: bool) -> str:
        """DingTalk wants a `### heading`; WeCom renders bold instead.

        Unlike the Feishu card, this dialect has no plain_text half — the
        headline and the footer are markdown here too, so both are escaped.
        """
        headline = escape_markup(self.headline)
        lines = [f"### {headline}" if heading else f"**{headline}**"]
        if self.summary:
            lines.append(f"{escape_markup(self.level_tag)} {escape_markup(self.summary)}")
        crumb = self.breadcrumb()
        if crumb:
            lines.append(escape_markup(crumb))
        impact = str(self.analysis.get("impact_scope") or "")
        if impact:
            lines.append(f"**Impact**: {escape_markup(impact)}")
        for link in self.links:
            rendered = self._link(link)
            if rendered:
                lines.append(rendered)
        # No buttons: these bots have no callback channel, and a button that
        # does nothing is worse than no button. The links still travel.
        footer = self.footer()
        if footer:
            lines.append(f"> {escape_markup(footer)}")
        return "\n\n".join(lines)
