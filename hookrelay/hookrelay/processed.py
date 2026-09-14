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
      "analysis": {"summary", "detail", "event_type", "impact_scope", "confidence"},
      "identity": {"project": "...", "env": "prod", ...},   # meaningful values
      "links":    [{"text": "...", "url": "..."}],
      "actions":  [{"text": "Acknowledge", "value": {...}}]   # pre-signed by the brain
    }

and the pipe turns it into ONE thing: the card model (`card_model`,
docs/bridge-protocol.md) — headline, state, lead, summary, identity crumb,
impact, links, actions, footer, as plain facts. Who renders that into a
platform's dialect is not this module's business: a bridge sidecar for a chat
that calls back, a plugin (examples/plugins/chat_markdown_channels.py) for a
markdown webhook. `actions` are carried as the brain signed them: the value is
opaque, because a signature is judgement about identity, not formatting.

The Feishu card renderer used to live here, then WeCom's and DingTalk's — the
pipe knowing three platforms' schemas so that no brain had to know one. The
protocol keeps the second half of that bargain without the first.

Rendering lives here rather than in each builder so the five blocks stay in one
place: headline, identity breadcrumb, impact, links, footer.
"""

from __future__ import annotations

import time
from typing import Any

_LEVEL_TAG = {
    "critical": "🔴 CRITICAL",
    "high": "🔴 HIGH",
    "warning": "🟠 MEDIUM",
    "medium": "🟠 MEDIUM",
    "low": "🔵 LOW",
}


def _as_dict(value: Any) -> dict[str, Any]:
    """The wire is untyped: a dict where an object was expected, else empty."""
    return value if isinstance(value, dict) else {}


class Processed:
    """A brain's result, with the accessors every renderer needs."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.raw = payload if isinstance(payload, dict) else {}
        # `.get("meta") or {}` still returns a str/list when the sender put one
        # there, and the accessors below then call .get() on it → AttributeError,
        # which escapes send()'s narrow except and dead-letters nothing: the row
        # is retried every tick forever, head-of-line blocking its channel. Coerce
        # to a dict so a malformed field renders as absent, not as a poison pill.
        self.meta: dict[str, Any] = _as_dict(self.raw.get("meta"))
        self.analysis: dict[str, Any] = _as_dict(self.raw.get("analysis"))
        self.identity: dict[str, Any] = _as_dict(self.raw.get("identity"))
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

    # ── the neutral model: what a bridge or a plugin renders, in no dialect ──
    def card_model(self) -> dict[str, Any]:
        """The card as facts, not markup — the shape in docs/bridge-protocol.md.

        Plain text throughout: whoever renders this into a platform's dialect is
        the one place that knows what needs escaping there. Actions travel as the
        brain signed them. `tone` names the STATE; the colour it earns is the
        renderer's decision, so a recovery is "recovery" here and green wherever
        it lands.
        """
        model: dict[str, Any] = {
            "title": self.headline,
            "tone": "recovery" if self.is_recovery else self.importance,
            "lead": self.level_tag,
            "summary": self.summary,
            "crumb": self.breadcrumb(),
            "impact": str(self.analysis.get("impact_scope") or ""),
            "links": [{"text": str(x.get("text") or ""), "url": str(x.get("url") or "")} for x in self.links],
            "actions": [
                {
                    "text": str(a.get("text") or "Action"),
                    "style": str(a.get("style") or "default"),
                    "value": a.get("value") or {},
                }
                for a in self.actions
            ],
            "footer": self.footer(),
        }
        return {key: value for key, value in model.items() if value not in ("", [], {})}
