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

import re
from dataclasses import dataclass, field
from typing import Any

# Markers that say a condition ENDED. Used for two different questions, which
# is why they live in one place: "is this a recovery?" and "which firing alert
# is this the recovery OF?"
_RECOVERY_WORDS = ("已恢复", "已解决", "恢复", "resolved", "recovered", "cleared")

# [RESOLVED] / [OK] / [已恢复] / 【恢复】 — the decoration monitoring systems add
# to an otherwise unchanged title.
_MARKER_BRACKETED = re.compile(
    r"[\[\(（【]\s*(?:" + "|".join(_RECOVERY_WORDS) + r"|ok)\s*[\]\)）】]",
    re.IGNORECASE,
)
_MARKER_EDGE = re.compile(
    r"(?:^\s*(?:" + "|".join(_RECOVERY_WORDS) + r"|ok)\b\s*[:：\-–·]*\s*)"
    r"|(?:\s*[:：\-–·]*\s*\b(?:" + "|".join(_RECOVERY_WORDS) + r"|ok)\s*$)",
    re.IGNORECASE,
)


def _dict_or_empty(value: Any) -> dict[str, Any]:
    """The wire is untyped; a dict where one was hoped for, else nothing."""
    return value if isinstance(value, dict) else {}


def condition_title(title: str) -> str:
    """The title with any "it ended" decoration removed.

    A recovery is almost always the firing alert's title plus a marker, so
    including the marker in the identity gives the pair two different
    identities — and then a recovery can never find the firing it belongs to.
    The recovery route was unreachable for exactly this reason: every recovery
    fell through to the rule floor and re-derived an importance, so a "high"
    alert would end with a "medium" recovery card. That is the contradiction
    this whole design says it prevents.
    """
    cleaned = _MARKER_BRACKETED.sub(" ", title)
    cleaned = _MARKER_EDGE.sub("", cleaned)
    return " ".join(cleaned.split()) or " ".join(title.split())


# Judgement routes, in the order the ledger reports them. Every judged event
# has exactly one, and it is the first question about cost: what did we
# actually pay for?
_FIRING_PREFIX = re.compile(r"^\[(?:FIRING|RESOLVED)(?::\d+)?\]\s*")

ROUTE_AI = "ai"  # a model was called
ROUTE_REUSE = "reuse"  # a prior verdict for the same identity was reused
ROUTE_RECOVERY = "recovery"  # the alert ended; reuse what its firing said
ROUTE_RULE = "rule"  # the model was unavailable or refused; rules decided
ROUTE_RULE_REUSE = "rule-reuse"  # a prior AI verdict for the same alert RULE answered

IMPORTANCE = ("critical", "high", "medium", "low")

# Fields that must never enter an identity. Identity answers ONE question —
# which condition is this? — so anything answering a different question about
# the same condition has to be excluded:
#
#   when did it happen   timestamps and per-delivery ids differ every time, so
#                        including them makes every alert unique, which is the
#                        same as having no reuse at all.
#   what state is it in  status/severity change while the condition does not.
#                        Alertmanager sends status=firing then status=resolved
#                        for one alert; keeping status split the pair into two
#                        identities and the recovery could not find its firing
#                        — the same defect as leaving [RESOLVED] in the title,
#                        arriving through a different door. A severity that
#                        escalates is one condition getting worse, not a new one.
_NON_IDENTITY_FIELDS = frozenset(
    {
        "timestamp",
        "time",
        "id",
        "event_id",
        "uuid",
        "status",
        "state",
        "alertstate",
        "severity",
        "level",
        "importance",
        "priority",
    }
)


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
    # The pipe's explicit recovery flag, when the upstream platform stated the
    # fact (None = nothing stated; fall back to keyword detection).
    recovery_flag: bool | None = None

    @classmethod
    def parse(cls, payload: dict[str, Any], *, now: float, correlation_id: str = "") -> Incoming:
        """Accepts both shapes the pipe can deliver.

        The wrapped {meta, event, raw} envelope above is the documented one,
        but hookrelay's `payload: normalized` channel puts the event FLAT on
        the wire — {event_id, source, title, body, level, fields, received_at}
        — and strips the original payload before signing. Reading only the
        wrapped shape parsed every real delivery into empty strings, which is
        not a blank verdict but a WRONG one: identity collapses to the same
        constant for every alert, so the second event and all after it reuse
        the first one's judgement forever. It even looks healthy from the
        outside, because a paid ratio near zero reads as excellent savings.
        """
        meta = _dict_or_empty(payload.get("meta"))
        wrapped = payload.get("event")
        event = wrapped if isinstance(wrapped, dict) else payload
        fields = _dict_or_empty(event.get("fields"))
        recovery_raw = event.get("is_recovery", meta.get("is_recovery"))
        recovery_flag: bool | None
        if isinstance(recovery_raw, bool):
            recovery_flag = recovery_raw
        elif isinstance(recovery_raw, str) and recovery_raw.strip():
            recovery_flag = recovery_raw.strip().lower() in ("true", "1", "yes", "resolved", "recovery", "recovered")
        else:
            recovery_flag = None
        return cls(
            source=str(meta.get("source") or event.get("source") or "unknown"),
            title=str(event.get("title") or ""),
            body=str(event.get("body") or ""),
            level=str(event.get("level") or "").lower(),
            fields={str(k): str(v) for k, v in fields.items() if str(v).strip()},
            raw=_dict_or_empty(payload.get("raw")),
            # Precedence: the body says it, else the transport header did, else
            # the pipe's own hr-<event_id> convention. The header matters
            # because the flat shape carries no correlation id at all, and
            # without one the pipe cannot group the judgement with its origin.
            correlation_id=str(
                meta.get("correlation_id")
                or payload.get("correlation_id")
                or correlation_id
                or (f"hr-{event['event_id']}" if str(event.get("event_id") or "").strip() else "")
            ),
            received_at=float(meta.get("received_at") or event.get("received_at") or now),
            recovery_flag=recovery_flag,
        )

    @property
    def rule_key(self) -> str:
        """Which alert rule this is, as opposed to which firing of it.

        Identity separates instances on purpose — two hosts hitting the same
        threshold are two conditions. A judgement, though, is usually about the
        rule: measured on 795 real alerts, 28 of 29 rules had one and only one
        AI verdict across every firing. So the rule is the key worth reusing a
        paid answer on, and Grafana/Alertmanager both name it in a label. The
        title is the fallback, minus the "[FIRING:2]" prefix that would
        otherwise split one rule into firing and resolved halves.
        """
        for label in ("alertname", "rulename", "RuleName"):
            named = str(self.fields.get(label) or "").strip()
            if named:
                return named
        return _FIRING_PREFIX.sub("", self.title).strip()

    @property
    def identity(self) -> str:
        """What makes two events the SAME condition rather than two events.

        The title plus the identity-ish fields — deliberately not the whole
        payload, whose timestamps and sequence numbers differ every time and
        would make every alert unique (which is the same as having no reuse).

        The title is normalized (see condition_title): a firing alert and its
        recovery are ONE condition, and they must share an identity or the
        recovery cannot be linked to what it recovered from.
        """
        parts = [self.source, condition_title(self.title)]
        for key in sorted(self.fields):
            if key.lower() in _NON_IDENTITY_FIELDS:
                continue
            parts.append(f"{key}={self.fields[key]}")
        return "|".join(parts)

    @property
    def is_recovery(self) -> bool:
        """Did the condition END? Recovery is a fact about the alert, not an
        opinion, so it is read here and never asked of the model.

        A pipe that carries the upstream platform's explicit flag wins over
        keyword sniffing: a recovery whose body is a reused firing summary
        (WebhookWise's relay envelope does exactly this) contains no recovery
        word at all, and sniffing called it a fresh firing."""
        if self.recovery_flag is not None:
            return self.recovery_flag
        haystack = f"{self.title} {self.body} {' '.join(self.fields.values())}".lower()
        # "ok" only counts as a standalone word: "okhttp timeout" is not a recovery.
        words = set(haystack.replace("[", " ").replace("]", " ").replace(":", " ").split())
        return any(word in haystack for word in _RECOVERY_WORDS) or "ok" in words


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
                # The CONDITION's name, with any "it ended" decoration removed.
                # State travels separately in is_recovery, and the pipe renders
                # it ("✅ Resolved · <name>"). Sending the raw title made the
                # recovery card say so twice: "✅ Resolved · [RESOLVED] Payment…".
                # One fact, one field.
                "alert_name": condition_title(self.incoming.title),
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
