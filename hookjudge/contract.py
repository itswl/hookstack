"""The two shapes at the edges. Nothing else crosses the boundary.

hookjudge sits behind a pipe (hookrelay) and does exactly one thing: it
judges. The pipe adapts every upstream dialect on the way in and builds every
downstream format on the way out, so this service never learns what Grafana
sends or what a Feishu card looks like.

IN — the pipe's normalized event:

    {"meta":  {"source", "correlation_id", "received_at", "template"},
     "event": {"title", "body", "level", "fields": {...}},
     "raw":   {...}}                      # the original, for analysis context

OUT — the judgement, posted back to a pipe door:

    {"meta":     {"alert_name", "source", "importance", "brain",
                  "correlation_id", "is_recovery", "timestamp"},
     "analysis": {"summary", "event_type", "impact_scope", "importance"},
     "identity": {...},                   # the fields the pipe should lay out
     "links":    []}

Both are the pipe's published shapes. Keeping them in one file means a change
to either is a change you can SEE, rather than a field quietly appearing in a
dict three layers down.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Judgement routes, in the order the ledger reports them. Every judged event
# has exactly one, and it is the first question about cost: what did we
# actually pay for?
ROUTE_AI = "ai"  # a model was called
ROUTE_REUSE = "reuse"  # a prior verdict for the same identity was reused
ROUTE_RECOVERY = "recovery"  # the alert ended; reuse what its firing said
ROUTE_RULE = "rule"  # the model was unavailable or refused; rules decided

IMPORTANCE = ("critical", "high", "medium", "low")


@dataclass(frozen=True, slots=True)
class Incoming:
    """One normalized event from the pipe."""

    source: str
    title: str
    body: str
    level: str
    fields: dict[str, str]
    raw: dict[str, Any]
    correlation_id: str
    received_at: float

    @classmethod
    def parse(cls, payload: dict[str, Any], *, now: float) -> Incoming:
        meta = payload.get("meta") or {}
        event = payload.get("event") or {}
        fields = event.get("fields") or {}
        return cls(
            source=str(meta.get("source") or event.get("source") or "unknown"),
            title=str(event.get("title") or ""),
            body=str(event.get("body") or ""),
            level=str(event.get("level") or "").lower(),
            fields={str(k): str(v) for k, v in fields.items() if str(v).strip()},
            raw=payload.get("raw") if isinstance(payload.get("raw"), dict) else {},
            correlation_id=str(meta.get("correlation_id") or ""),
            received_at=float(meta.get("received_at") or now),
        )

    @property
    def identity(self) -> str:
        """What makes two events the SAME condition rather than two events.

        The title plus the identity-ish fields — deliberately not the whole
        payload, whose timestamps and sequence numbers differ every time and
        would make every alert unique (which is the same as having no reuse).
        """
        parts = [self.source, self.title]
        for key in sorted(self.fields):
            if key in ("timestamp", "time", "id", "event_id", "uuid"):
                continue
            parts.append(f"{key}={self.fields[key]}")
        return "|".join(parts)

    @property
    def is_recovery(self) -> bool:
        """Did the condition END? Recovery is a fact about the alert, not an
        opinion, so it is read here and never asked of the model."""
        haystack = f"{self.title} {self.body} {' '.join(self.fields.values())}".lower()
        markers = ("resolved", "recovered", "ok", "cleared", "恢复", "已解决", "已恢复")
        # "ok" only counts as a standalone word: "okhttp timeout" is not a recovery.
        words = set(haystack.replace("[", " ").replace("]", " ").replace(":", " ").split())
        return any(m in haystack for m in markers if m != "ok") or "ok" in words


@dataclass(frozen=True, slots=True)
class Verdict:
    """What this service decided. No colours, no markdown, no card schema."""

    summary: str
    importance: str
    event_type: str = ""
    impact_scope: str = ""
    route: str = ROUTE_RULE
    tokens_in: int = 0
    tokens_out: int = 0
    cost: float = 0.0
    model: str = ""
    degraded_reason: str = ""

    def normalized(self) -> Verdict:
        importance = self.importance.strip().lower()
        return Verdict(
            summary=self.summary.strip(),
            importance=importance if importance in IMPORTANCE else "medium",
            event_type=self.event_type.strip(),
            impact_scope=self.impact_scope.strip(),
            route=self.route,
            tokens_in=self.tokens_in,
            tokens_out=self.tokens_out,
            cost=self.cost,
            model=self.model,
            degraded_reason=self.degraded_reason,
        )


@dataclass(frozen=True, slots=True)
class Outgoing:
    """The result envelope the pipe knows how to dress."""

    incoming: Incoming
    verdict: Verdict
    brain: str = "hookjudge"
    links: list[dict[str, str]] = field(default_factory=list)

    def payload(self) -> dict[str, Any]:
        return {
            "meta": {
                "brain": self.brain,
                "alert_name": self.incoming.title,
                "source": self.incoming.source,
                "importance": self.verdict.importance,
                "correlation_id": self.incoming.correlation_id,
                "is_recovery": self.incoming.is_recovery,
                "timestamp": self.incoming.received_at,
                "route": self.verdict.route,
            },
            "analysis": {
                "summary": self.verdict.summary,
                "event_type": self.verdict.event_type,
                "impact_scope": self.verdict.impact_scope,
                "importance": self.verdict.importance,
            },
            # Identity as DATA: choosing separators and order is layout, and
            # layout belongs to the pipe.
            "identity": dict(self.incoming.fields),
            "links": list(self.links),
        }
