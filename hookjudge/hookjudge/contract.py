"""The two shapes at the edges. Nothing else crosses the boundary.

hookjudge sits behind a pipe (hookrelay) and does exactly one thing: it
judges. The pipe adapts every upstream dialect on the way in and builds every
downstream format on the way out, so this service never learns what Grafana
sends or what a Feishu card looks like.

IN — the pipe's normalized event:

    {"meta":  {"source", "correlation_id", "received_at", "template"},
     "event": {"title", "body", "level", "fields": {...}},
     "raw":   {...}}                      # the original — DELIBERATELY ignored

`raw` is read off the wire by nothing here. It was parsed and held for a while
as "analysis context", and reaching neither the prompt nor the ledger it was a
claim rather than a feature. Using it would also be the wrong direction: the
untrusted span in the prompt is bounded on purpose (see build_ai_request), and
the original payload is the one part of a delivery whose size and shape nobody
normalized.

OUT — the judgement, posted back to a pipe door:

    {"meta":     {"alert_name", "source", "importance", "brain",
                  "correlation_id", "is_recovery", "timestamp"},
     "analysis": {"summary", "event_type", "impact_scope", "importance"},
     "identity": {...},                   # the fields the pipe should lay out
     "links":    [],                      # always empty; the pipe owns links
     "actions":  [{"kind", "text", ...}]} # which buttons this verdict deserves

Both are the pipe's published shapes. Keeping them in one file means a change
to either is a change you can SEE, rather than a field quietly appearing in a
dict three layers down.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
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

# [FIRING] / [FIRING:2] / [RESOLVED:2] — Alertmanager's own notification
# templates put the group state in front of the title, and the `:N` form
# carries the group size. Neither belongs in an identity: the pair
# "[FIRING:2] Payment API down" / "[RESOLVED:2] Payment API down" is ONE
# condition, and leaving the prefix in gave it two identities, which stranded
# every recovery on the rule floor. _MARKER_BRACKETED cannot do this job — it
# only knows the recovery words, so it strips [RESOLVED] and leaves [FIRING].
_FIRING_PREFIX = re.compile(r"^\[(?:FIRING|RESOLVED)(?::\d+)?\]\s*", re.IGNORECASE)

# A recovery word only counts where the text ASSERTS the condition ended.
# Three ways that went wrong, each verified against live wording:
#   "Unresolved deadlocks climbing on db-1"  — inside a longer word
#   "Backlog not cleared for 30m"            — under a negation
#   "恢复中: 支付网关仍在重试"                  — recovering, not recovered
# Latin words anchor on word boundaries; the CJK ones have no boundary to
# anchor to and match on containment, so they lean on the guards below.
_RECOVERY_MENTION = re.compile(
    "|".join(rf"\b{re.escape(word)}\b" if word.isascii() else re.escape(word) for word in _RECOVERY_WORDS),
    re.IGNORECASE,
)
_NEGATED_BEFORE = re.compile(r"(?:not|isn't|is not|hasn't|has not|no longer|未|尚未|没有|无法)\s*\W*$", re.IGNORECASE)
# 恢复中 / 恢复了一部分 — the condition is still moving.
_IN_PROGRESS_AFTER = ("中",)


def _asserts_recovery(text: str) -> bool:
    """Does this text say the condition ENDED, rather than that it is still going?"""
    for match in _RECOVERY_MENTION.finditer(text):
        preceding = text[max(0, match.start() - 24) : match.start()]
        if _NEGATED_BEFORE.search(preceding):
            continue
        if text[match.end() :].startswith(_IN_PROGRESS_AFTER):
            continue
        return True
    return False


def _dict_or_empty(value: Any) -> dict[str, Any]:
    """The wire is untyped; a dict where one was hoped for, else nothing."""
    return value if isinstance(value, dict) else {}


def _epoch_or(value: Any, fallback: float) -> float:
    """A receipt time off the wire, as seconds since the epoch.

    Bare float() was called on whatever the sender put there. Every upstream
    this service has met sends epoch seconds — but ISO-8601 is what the rest of
    the monitoring world sends, and one signed delivery carrying
    "2026-08-19T09:14:02Z" raised ValueError INSIDE ingest: a 500 to the pipe
    and an alert dropped on the floor, for a field that is only ever used to
    order rows.

    ISO-8601 is parsed rather than discarded, because received_at drives the
    reuse window and retention — throwing it away would file a replayed capture
    as brand new. A naive stamp is read as UTC: that is what these systems emit,
    and guessing the process's local zone would be a silent hour-scale error.
    """
    if isinstance(value, bool) or value is None or value == "":
        return fallback
    try:
        return float(value)
    except (TypeError, ValueError):
        pass
    try:
        stamp = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return fallback
    return (stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)).timestamp()


def condition_title(title: str) -> str:
    """The title with any "it ended" decoration removed.

    A recovery is almost always the firing alert's title plus a marker, so
    including the marker in the identity gives the pair two different
    identities — and then a recovery can never find the firing it belongs to.
    The recovery route was unreachable for exactly this reason: every recovery
    fell through to the rule floor and re-derived an importance, so a "high"
    alert would end with a "medium" recovery card. That is the contradiction
    this whole design says it prevents.

    The group-state prefix goes first, because it decorates BOTH sides of the
    pair — [FIRING:2] on the firing, [RESOLVED:2] on its recovery — and only
    the recovery half looks like a recovery word.
    """
    cleaned = _FIRING_PREFIX.sub("", title)
    cleaned = _MARKER_BRACKETED.sub(" ", cleaned)
    cleaned = _MARKER_EDGE.sub("", cleaned)
    return " ".join(cleaned.split()) or " ".join(title.split())


# Judgement routes, in the order the ledger reports them. Every judged event
# has exactly one, and it is the first question about cost: what did we
# actually pay for?
ROUTE_AI = "ai"  # a model was called
ROUTE_REUSE = "reuse"  # a prior verdict for the same identity was reused
ROUTE_RECOVERY = "recovery"  # the alert ended; reuse what its firing said
ROUTE_RULE = "rule"  # the model was unavailable or refused; rules decided
ROUTE_RULE_REUSE = "rule-reuse"  # a prior AI verdict for the same alert RULE answered

IMPORTANCE = ("critical", "high", "medium", "low")

# Upstream severity words that MEAN one of ours. Prometheus, Alertmanager and
# Grafana all say `warning` where this vocabulary says `medium`, and the rule
# floor has always read it that way — but the agreement ledger compared the two
# columns raw, so every `warning` row was scored as the judge overruling the
# platform. `warning` is the most common severity those systems emit, which made
# the shadow run's headline agreement number wrong for the majority case, in the
# flattering direction: it manufactured disagreements to review.
LEVEL_SYNONYMS = {"warning": "medium"}

# Every platform level that can be compared with a judge importance at all. A
# level outside this set ("info", "none", a vendor's own word) is not a verdict
# in this vocabulary, so comparing it would invent an opinion the platform never
# expressed.
COMPARABLE_LEVELS = IMPORTANCE + tuple(LEVEL_SYNONYMS)


def platform_importance(level: str) -> str:
    """The platform's severity, said in this service's four words.

    One map, three readers — the rule floor, the agreement matrix and the review
    queue. It was three copies of the intent and only one of them (the floor)
    actually held the mapping, which is how `warning` came to be equivalent to
    `medium` when a verdict was DERIVED from it and a disagreement when the two
    were COMPARED.
    """
    cleaned = level.strip().lower()
    return LEVEL_SYNONYMS.get(cleaned, cleaned)


# The buttons a verdict can ask for. Three, and no more: `silence` (stop
# restating this condition for a while) and the `useful`/`useless` pair (was
# being interrupted for this worth it). `followup` and `approve` are the
# investigator's — they answer "act on this report", and a verdict is not a
# proposal, so it has nothing to approve.
ACTION_SILENCE = "silence"
ACTION_USEFUL = "useful"
ACTION_USELESS = "useless"
ACTION_KINDS = (ACTION_SILENCE, ACTION_USEFUL, ACTION_USELESS)

# How long a verdict is willing to stop talking about its own condition, by how
# bad it just said that condition is. The window IS the judgement: nobody but
# the brain knows whether four hours of quiet is a relief or a missed outage.
_SILENCE_MINUTES = {"critical": 15, "high": 15, "medium": 60, "low": 240}

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
        # The platform's triage verdict rides along for three-way comparison
        # (platform importance / platform triage / judge importance). It can
        # flip between a firing and its recovery, so it must never split
        # identity.
        "triage_verdict",
        "triage_confidence",
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
            received_at=_epoch_or(meta.get("received_at") or event.get("received_at"), now),
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
        return _asserts_recovery(haystack) or "ok" in words


@dataclass(frozen=True, slots=True)
class Verdict:
    """What this service decided. No colours, no markdown, no card schema."""

    summary: str
    importance: str
    event_type: str = ""
    impact_scope: str = ""
    # The second axis. `importance` says how serious the subject is; this says
    # whether a person has anything to do about it now. Measured on production,
    # the judge answered `high` for 210 of 216 alerts — an importance classifier
    # that agrees with itself 97% of the time carries almost no information, and
    # its own prompt is why: 74% of this traffic is payments, and payments
    # default to high. The question the product needs was never being asked.
    #
    # Kept out of `importance` and out of `mattered`: the first is a different
    # question, the second means a HUMAN said so and is the one field here that
    # does. Its own field, its own column, its own count.
    wake_someone: str = ""
    route: str = ROUTE_RULE
    tokens_in: int = 0
    tokens_out: int = 0
    cost: float = 0.0
    model: str = ""
    degraded_reason: str = ""

    def normalized(self) -> Verdict:
        importance = self.importance.strip().lower()
        wake = self.wake_someone.strip().lower()
        return Verdict(
            summary=self.summary.strip(),
            importance=importance if importance in IMPORTANCE else "medium",
            event_type=self.event_type.strip(),
            impact_scope=self.impact_scope.strip(),
            # Blank when the model did not answer, never guessed: an unanswered
            # axis has to be distinguishable from a "no", or the count below it
            # silently treats every parse failure as a quiet night.
            wake_someone=wake if wake in ("yes", "no") else "",
            route=self.route,
            tokens_in=self.tokens_in,
            tokens_out=self.tokens_out,
            cost=self.cost,
            model=self.model,
            degraded_reason=self.degraded_reason,
        )


def _silence_text(minutes: int) -> str:
    """Scaled to the unit, like every other duration the family prints: 90m is a
    number nobody reads as an hour and a half."""
    return f"Silence {minutes}m" if minutes % 60 else f"Silence {minutes // 60}h"


def declared_actions(verdict: Verdict, *, is_recovery: bool) -> list[dict[str, Any]]:
    """Which buttons a card deserves — a judgement, so it is made here.

    Declaring is all this service does: the pipe mints the signed token behind
    the button and owns the callback, because a signature is the channel edge's
    business and this brain never holds a secret. The pipe also drops any kind
    it has not been configured to accept, so every entry below is a request.

    A RECOVERY declares nothing. There is nothing to silence — the condition
    already ended, and a window opened on it would land on the next genuine
    firing, which is the "a mute hides an escalation" failure arriving through a
    door nobody was watching. Nor is the feedback pair coherent there: "was
    waking me for this worth it" on a resolution notice asks whether the channel
    should send resolution notices at all, and that is a channel setting the pipe
    owns, not something the judge can learn about this condition.

    Everything else declares all three, the free routes included. That is
    deliberate. A storm of twelve restatements interrupted a human twelve times,
    and eleven of those cards costing nothing is exactly the contrast the cost
    figures hide; withholding the pair from them would leave the noise unrulable
    and "I was interrupted 40 times and 3 mattered" uncomputable, which is the
    sentence the measurement beside this exists to answer.

    Route is not read here, on purpose. Offering a longer mute on a restatement
    than on a first firing would be the judge quietly writing a suppression
    policy, and who owns noise when a verdict is reused is a decision left open
    on the record (.agents/notes/proposed/2026-08-12-who-owns-noise-when-a-
    verdict-is-reused.md). Only importance and is_recovery are read, and both of
    them survive a round trip through the ledger — which the return leg depends
    on, since it rebuilds this payload from a stored row and must declare the
    same buttons it would have declared the first time.
    """
    if is_recovery:
        return []
    # 15 minutes is still offered on a critical alert rather than nothing. An
    # operator working an incident is getting the same card every thirty seconds,
    # and when the card offers no way to stop it the alternative they reach for
    # is muting the whole channel — which hides everything, not just this.
    minutes = _SILENCE_MINUTES.get(verdict.importance, 60)
    return [
        {"kind": ACTION_SILENCE, "text": _silence_text(minutes), "minutes": minutes},
        {"kind": ACTION_USEFUL, "text": "Worth waking me"},
        {"kind": ACTION_USELESS, "text": "Not worth it"},
    ]


@dataclass(frozen=True, slots=True)
class Outgoing:
    """The result envelope the pipe knows how to dress."""

    incoming: Incoming
    verdict: Verdict
    brain: str = "hookjudge"

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
                # The second axis travels with the verdict so the pipe can ROUTE
                # on it — a "nobody needs to act now" that only ever reached a
                # board was an answer this service paid for and nothing used.
                # '' when unanswered, and the pipe must treat '' as deliverable:
                # fail open, never quiet on a parse failure.
                "wake_someone": self.verdict.wake_someone,
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
            # Present and empty, always. The key is part of the pipe's published
            # shape, so it stays; a settable field was not, because a link is a
            # URL into somebody's console and this brain is given none — the
            # pipe, which knows the channel, is the one that can build them.
            "links": [],
            # Which buttons this verdict deserves. A card used to be a dead end:
            # the operator could read it and do nothing. What each button MEANS
            # is judgement and lives here; the token behind it and the callback
            # that catches the press are the pipe's.
            "actions": declared_actions(self.verdict, is_recovery=self.incoming.is_recovery),
        }
