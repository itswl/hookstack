"""Routing configuration: sources, pipeline, channels — loaded from one YAML file.

A source is an inbound door (which ADAPTER verifies and reads it).
The pipeline is the ordered list of PROCESSORS every event walks.
A channel is an outbound pipe (which channel TYPE builds the wire format).

All three reference registry names, so plugins extend the vocabulary without
touching this file's schema. Unknown names fail AT BOOT — config errors must
stop the process, never the first event.

Secrets never sit in the YAML: any value written as ${NAME} resolves from the
environment at load time, so the file itself is safe to commit.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, replace
from typing import Any

import yaml

from hookrelay import actions, registry
from hookrelay.templates import ExtractTemplate, TemplateSelector

_ENV_REF = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


class ConfigError(ValueError):
    """A configuration mistake worth stopping the process for."""


def _resolve(value: Any) -> Any:
    if isinstance(value, str):
        match = _ENV_REF.match(value.strip())
        if match:
            return os.environ.get(match.group(1), "")
    return value


def _resolve_deep(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _resolve_deep(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_deep(v) for v in value]
    return _resolve(value)


# Routing speaks these three keys natively; an extracted field of the same
# name would silently shadow one (the routing context merges fields last), so
# the collision is refused at load time instead of surprising someone at 3am.
RESERVED_FIELD_NAMES = ("source", "level", "title")

# Extracted keys that exist on every event whatever the templates say, and are
# read straight off it rather than out of `fields` (see extract.fingerprint).
BUILTIN_EXTRACTED_NAMES = ("title", "body", "level")

# Processors that cannot put a new name into an event's fields. The fingerprint
# vocabulary is fully knowable only when the walk up to the fingerprint is made
# of these (plus `set`, whose additions are written down in the config itself).
_FIELD_PRESERVING_PROCESSORS = frozenset({"dedup", "silence", "routes", "filter"})


@dataclass(frozen=True, slots=True)
class Source:
    name: str
    secret: str  # empty = unsigned source (document why before doing that)
    title: str
    body: str
    level: str
    # Optional recovery-flag template (see ExtractTemplate.recovery).
    recovery: str = ""
    # Ordered extraction templates; one door, many payload shapes. Always at
    # least one entry (the inline title/body/level form becomes template
    # "inline"), so selection can never come up empty.
    templates: tuple[ExtractTemplate, ...] = ()
    adapter: str = "default"
    level_map: dict[str, str] = field(default_factory=dict)
    fields: dict[str, str] = field(default_factory=dict)
    # Which extracted keys form the duplicate fingerprint. Empty = title+body.
    fingerprint_fields: tuple[str, ...] = ()
    dedup_window_seconds: int = 120
    # Storm fuse (volume, not content — see hookrelay/fuse.py). 0 = no fuse.
    # Mandatory reading: a brain-paired deploy may rely on the brain's own
    # backpressure, but a relay in front of something WITHOUT backpressure
    # (a brain without its own storm gate) must carry its own fuse.
    storm_threshold: int = 0
    storm_window_seconds: int = 60
    # Replay protection. require_timestamp refuses the legacy body-only
    # signature: turn it on once every sender for this door sends
    # X-Hook-Timestamp (senders migrate first, then the door closes).
    require_timestamp: bool = False
    max_skew_seconds: int = 300
    # Free-form bag for custom adapters (the core never reads it).
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Channel:
    name: str
    type: str
    url: str
    secret: str = ""
    # 0 = unlimited. Enforced at delivery time by deferring, never dropping.
    max_per_minute: int = 0
    # Outbound signature header name (generic type); lets hookrelay speak a
    # receiver's dialect — e.g. X-Webhook-Signature for receivers that expect it.
    signature_header: str = "X-Hook-Signature"
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Route:
    name: str
    source: str  # "*" matches every source
    send_to: tuple[str, ...]
    when: dict[str, Any] = field(default_factory=dict)
    priority: int = 0
    stop: bool = False


@dataclass(frozen=True, slots=True)
class PipelineStage:
    name: str  # display name in traces; defaults to the type
    type: str  # processor registry key
    options: dict[str, Any] = field(default_factory=dict)


DEFAULT_PIPELINE: tuple[PipelineStage, ...] = (
    PipelineStage(name="dedup", type="dedup"),
    PipelineStage(name="silence", type="silence"),
    PipelineStage(name="routes", type="routes"),
)


# The judgment features, named by the doctrine in README.md as belonging to
# STANDALONE posture — a small team with no brain behind the relay. That section
# also says that in paired posture "they should all yield", and until now that
# sentence had no way to make itself heard: a config could put dedup in front of
# a brain forever and nothing would mention it.
#
# Measured on 2026-08-20 (see .agents/notes/proposed/
# 2026-08-20-what-standalone-posture-actually-costs-the-pipe.md): these three
# are 123 source lines, 2.9% of the pipe, and removing them would withdraw five
# documented config keys and one of the service's two stated promises. So they
# stay, and the doctrine gets a voice instead of a deletion.
_STANDALONE_STAGES = ("dedup", "set", "filter")


def _warn_posture_mix(pipeline: tuple[PipelineStage, ...], channels: dict[str, Channel]) -> str:
    """The one combination the doctrine says should not exist, or "".

    A brain is in play when the pipeline hands events to one (`http`) or when a
    channel expects a brain's RESULT back (`payload: processed`). Either way its
    noise accounting is the one that has to stay truthful, and a judgment stage
    in front of it silently changes what the brain is counting.

    A warning and not an error: a deployment mid-migration legitimately runs
    both for a while, and refusing to boot over a posture preference would be
    the pipe overruling its operator. Returned as a string rather than logged
    here so from_dict stays free of side effects and a test can read it.
    """

    def _downstream_of_the_brain(stage: PipelineStage) -> bool:
        """A filter that only ever matches a brain's RETURN is the brain deciding.

        The doctrine's objection is to a stage that changes what the brain is
        counting — one that stands BETWEEN the alert and the brain. A filter
        pinned to one source and matching on a field of the verdict (`wake`,
        the judge's own answer) drops nothing the brain will ever see; it
        enforces the brain's answer on the delivery leg. That is the paired
        shape working, not a posture mix, and warning about it every boot
        would teach operators to ignore this warning.
        """
        if stage.type != "filter":
            return False
        when = stage.options.get("when") or {}
        return bool(when.get("source")) and "wake" in when

    judging = [
        stage.name for stage in pipeline if stage.type in _STANDALONE_STAGES and not _downstream_of_the_brain(stage)
    ]
    if not judging:
        return ""
    reasons = []
    if any(stage.type == "http" for stage in pipeline):
        reasons.append("an http stage hands events to a brain")
    if any(str(ch.options.get("payload") or "") == "processed" for ch in channels.values()):
        reasons.append("a channel renders a brain's result (payload: processed)")
    if not reasons:
        return ""
    return (
        f"posture mix: the pipeline runs {', '.join(judging)} while {' and '.join(reasons)}. "
        "Those stages are standalone-posture features (see the doctrine in hookrelay/README.md) "
        "and in a paired deployment they should yield — the brain owns noise accounting, and a "
        "judgment stage in front of it changes what it is counting without telling it. "
        "Intentional during a migration; worth removing once the brain is the one deciding."
    )


@dataclass(frozen=True, slots=True)
class CardAction:
    """One kind of button this deployment is willing to put on a card.

    A brain DECLARES which actions its verdict deserves; this decides which of
    those a deployment actually offers, and where the press goes. Absent means
    not offered — a card cannot grow a button nobody configured, which is what
    keeps `approve` (it runs commands) an opt-in rather than a default.

    forward_to names a channel: an action press is delivered exactly like any
    other outbound message, so it inherits the retry, the rate limit and the
    ledger row instead of becoming a second delivery mechanism. `silence` is the
    exception — the pipe owns silences, so it needs no channel to reach.
    """

    kind: str
    forward_to: str = ""
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Escalation:
    """Deliver somewhere else when a card nobody touched goes cold.

    This is the smallest honest answer to the failure the family never had one
    for: an alert is judged well, dressed well, delivered well — and then nobody
    is awake. There is no on-call rotation here and deliberately so (see
    .agents/notes/proposed/2026-08-19-nobody-owns-an-alert-and-no-component-picked-it-up),
    which is exactly why this asks a question that needs no identity model:
    after `after_minutes`, has ANY human touched this alert?

    "Touched" means a card action was pressed — the `card_actions` ledger is the
    only evidence this service has that a person was there, and it is why this
    could not be built before the buttons existed. A silence counts, a follow-up
    counts, a "not worth it" counts: all of them are somebody awake.

    OFF unless configured. An escalation that fires without being asked for is a
    second alert about the first alert, in the middle of the night.
    """

    after_minutes: int
    send_to: tuple[str, ...]
    # Only alerts this important are worth a second delivery. Everything is
    # eligible when empty, which is almost never what anyone wants.
    levels: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Config:
    sources: dict[str, Source]
    channels: dict[str, Channel]
    routes: tuple[Route, ...]  # kept sorted by priority, highest first
    pipeline: tuple[PipelineStage, ...] = DEFAULT_PIPELINE
    card_actions: dict[str, CardAction] = field(default_factory=dict)
    escalation: Escalation | None = None

    @classmethod
    def from_file(cls, path: str) -> Config:
        with open(path, encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Config:
        # Top-level named templates, referenced by doors. A door may also keep
        # its fields inline (the original single-shape form) — that stays valid
        # forever; it becomes a one-entry list called "inline".
        named_templates: dict[str, ExtractTemplate] = {}
        for item in raw.get("templates") or []:
            kind = str(item.get("kind", "extract"))
            if kind != "extract":
                raise ConfigError(f"template {item.get('name')!r}: unknown kind {kind!r} (only 'extract' exists today)")
            match = dict(item.get("match") or {})
            exists_raw = match.get("exists")
            if exists_raw is None:
                exists_paths: list[str] = []
            elif isinstance(exists_raw, list):
                exists_paths = [str(v) for v in exists_raw]
            else:
                exists_paths = [str(exists_raw)]
            any_raw = match.get("any_of")
            if any_raw is None:
                any_paths: list[str] = []
            elif isinstance(any_raw, list):
                any_paths = [str(v) for v in any_raw]
            else:
                any_paths = [str(any_raw)]
            selector = TemplateSelector(
                exists=tuple(exists_paths),
                equals={str(k): str(v) for k, v in (match.get("equals") or {}).items()},
                any_of=tuple(any_paths),
            )
            template = ExtractTemplate(
                name=str(item["name"]),
                title=str(item.get("title", "{title}")),
                body=str(item.get("body", "{body}")),
                level=str(item.get("level", "")),
                level_map={str(k).lower(): str(v) for k, v in (item.get("level_map") or {}).items()},
                fields={str(k): str(v) for k, v in (item.get("fields") or {}).items()},
                recovery=str(item.get("recovery", "")),
                selector=selector,
            )
            for reserved in RESERVED_FIELD_NAMES:
                if reserved in template.fields:
                    raise ConfigError(
                        f"template {template.name}: field {reserved!r} would shadow the routing key of the "
                        f"same name — rename it"
                    )
            if template.name in named_templates:
                raise ConfigError(f"duplicate template name: {template.name}")
            named_templates[template.name] = template

        sources: dict[str, Source] = {}
        for item in raw.get("sources") or []:
            src = Source(
                name=str(item["name"]),
                secret=str(_resolve(item.get("secret", "")) or ""),
                title=str(item.get("title", "{title}")),
                body=str(item.get("body", "{body}")),
                level=str(item.get("level", "")),
                recovery=str(item.get("recovery", "")),
                adapter=str(item.get("adapter", "default")),
                level_map={str(k).lower(): str(v) for k, v in (item.get("level_map") or {}).items()},
                fields={str(k): str(v) for k, v in (item.get("fields") or {}).items()},
                fingerprint_fields=tuple(str(name) for name in (item.get("fingerprint_fields") or ())),
                dedup_window_seconds=int(item.get("dedup_window_seconds", 120)),
                storm_threshold=max(0, int(item.get("storm_threshold", 0))),
                storm_window_seconds=max(1, int(item.get("storm_window_seconds", 60))),
                require_timestamp=bool(item.get("require_timestamp", False)),
                max_skew_seconds=max(1, int(item.get("max_skew_seconds", 300))),
                options=_resolve_deep(dict(item.get("options") or {})),
            )
            for reserved in RESERVED_FIELD_NAMES:
                if reserved in src.fields:
                    raise ConfigError(
                        f"source {src.name}: field {reserved!r} would shadow the routing key of the same "
                        f"name — rename it"
                    )
            requested = [str(name) for name in (item.get("templates") or [])]
            if requested:
                missing = [name for name in requested if name not in named_templates]
                if missing:
                    raise ConfigError(
                        f"source {src.name}: unknown template(s) {missing} (known: {sorted(named_templates)})"
                    )
                chosen = tuple(named_templates[name] for name in requested)
                # A door whose last template has a selector can face a payload
                # nothing claims; select() would then fall back to that last
                # one anyway, so say it out loud rather than let it surprise.
                if not chosen[-1].selector.is_fallback:
                    raise ConfigError(
                        f"source {src.name}: the last template ({chosen[-1].name}) must have no match "
                        f"selector — it is the fallback for payloads nothing else claims"
                    )
            else:
                chosen = (
                    ExtractTemplate(
                        name="inline",
                        title=src.title,
                        body=src.body,
                        level=src.level,
                        level_map=src.level_map,
                        fields=src.fields,
                        recovery=src.recovery,
                    ),
                )
            src = replace(src, templates=chosen)
            if src.adapter not in registry.SOURCE_ADAPTERS:
                raise ConfigError(
                    f"source {src.name}: unknown adapter {src.adapter!r} (known: {sorted(registry.SOURCE_ADAPTERS)})"
                )
            if src.name in sources:
                raise ConfigError(f"duplicate source name: {src.name}")
            sources[src.name] = src

        channels: dict[str, Channel] = {}
        for item in raw.get("channels") or []:
            ch = Channel(
                name=str(item["name"]),
                type=str(item.get("type", "")),
                url=str(_resolve(item.get("url", "")) or ""),
                secret=str(_resolve(item.get("secret", "")) or ""),
                max_per_minute=int(item.get("max_per_minute", 0)),
                signature_header=str(item.get("signature_header", "X-Hook-Signature")),
                options=_resolve_deep(dict(item.get("options") or {})),
            )
            if ch.type not in registry.CHANNEL_BUILDERS:
                raise ConfigError(
                    f"channel {ch.name}: unknown type {ch.type!r} (known: {sorted(registry.CHANNEL_BUILDERS)})"
                )
            if not ch.url:
                raise ConfigError(f"channel {ch.name}: url is empty (env ref unset?)")
            if ch.name in channels:
                raise ConfigError(f"duplicate channel name: {ch.name}")
            channels[ch.name] = ch

        routes: list[Route] = []
        for item in raw.get("routes") or []:
            route = Route(
                name=str(item.get("name") or f"route-{len(routes) + 1}"),
                source=str(item.get("source", "*")),
                send_to=tuple(item.get("send_to") or ()),
                when=dict(item.get("when") or {}),
                priority=int(item.get("priority", 0)),
                stop=bool(item.get("stop", False)),
            )
            if route.source != "*" and route.source not in sources:
                raise ConfigError(f"route {route.name}: unknown source {route.source!r}")
            if not route.send_to:
                raise ConfigError(f"route {route.name}: send_to is empty")
            for channel_name in route.send_to:
                if channel_name not in channels:
                    raise ConfigError(f"route {route.name}: unknown channel {channel_name!r}")
            routes.append(route)
        routes.sort(key=lambda r: -r.priority)

        stages: list[PipelineStage] = []
        for item in raw.get("pipeline") or []:
            if isinstance(item, str):
                stage = PipelineStage(name=item, type=item)
            else:
                stage_type = str(item.get("type") or item.get("use") or "")
                options = {k: v for k, v in item.items() if k not in ("type", "use", "name")}
                stage = PipelineStage(
                    name=str(item.get("name") or stage_type),
                    type=stage_type,
                    options=_resolve_deep(options),
                )
            if stage.type == "http":
                from hookrelay.processors import MAX_INLINE_TIMEOUT_SECONDS

                requested_timeout = float(stage.options.get("timeout_seconds", 3.0))
                if requested_timeout > MAX_INLINE_TIMEOUT_SECONDS:
                    raise ConfigError(
                        f"pipeline stage {stage.name}: timeout_seconds={requested_timeout} exceeds the inline "
                        f"cap of {MAX_INLINE_TIMEOUT_SECONDS}s — an inline processor holds the sender's "
                        f"connection open, so a slower brain belongs in the async topology (deliver to it as "
                        f"a channel and let it re-enter through its own door)"
                    )
            if stage.type not in registry.PROCESSORS:
                raise ConfigError(
                    f"pipeline stage {stage.name}: unknown processor {stage.type!r} "
                    f"(known: {sorted(registry.PROCESSORS)})"
                )
            stages.append(stage)
        pipeline = tuple(stages) if stages else DEFAULT_PIPELINE
        if not any(stage.type == "routes" for stage in pipeline):
            # A router whose pipeline never routes is a misconfiguration, not
            # a preference — every event would end no_route.
            raise ConfigError("pipeline has no 'routes' stage")

        # fingerprint_fields must name something a door can actually read. A
        # typo there is the quietest total alert loss this service can suffer:
        # an unknown name resolves to "" for every event (extract.fingerprint),
        # so every alert of that source shares ONE fingerprint and everything
        # after the first is skipped as `duplicate` — which reads on the board
        # as excellent dedup and is a silent outage. Fails AT BOOT, like every
        # other name in this file.
        for src in sources.values():
            vocabulary = _fingerprint_vocabulary(src, pipeline)
            if vocabulary is None:
                continue  # an enrichment stage may add names nobody here knows
            unknown = [name for name in src.fingerprint_fields if name not in vocabulary]
            if unknown:
                raise ConfigError(
                    f"source {src.name}: fingerprint_fields names {unknown}, which nothing extracts for this "
                    f"door by the time the fingerprint is taken (available: {sorted(vocabulary)} — every "
                    f"template's fields, plus any `set` stage ahead of the dedup stage) — an unknown name "
                    f'resolves to "" for every event, so every alert would share one fingerprint and all but '
                    f"the first would be skipped as a duplicate"
                )

        # Card actions. Unknown kinds and unknown channels fail AT BOOT, like
        # every other name in this file: a button that 404s when an operator
        # finally presses it is worse than no button.
        card_actions: dict[str, CardAction] = {}
        for kind, spec in (raw.get("card_actions") or {}).items():
            name = str(kind).strip().lower()
            if name not in actions.KINDS:
                raise ConfigError(f"card_actions: unknown kind {name!r} (known: {', '.join(actions.KINDS)})")
            spec = spec or {}
            forward_to = str(spec.get("forward_to") or "")
            if forward_to and forward_to not in channels:
                raise ConfigError(f"card_actions.{name}: forward_to {forward_to!r} is not a configured channel")
            if not forward_to and name != "silence":
                raise ConfigError(
                    f"card_actions.{name}: needs forward_to — only 'silence' is something the pipe can do itself"
                )
            card_actions[name] = CardAction(kind=name, forward_to=forward_to, params=dict(spec.get("params") or {}))

        escalation: Escalation | None = None
        raw_escalation = raw.get("escalation") or {}
        if raw_escalation:
            after = int(raw_escalation.get("after_minutes") or 0)
            if after < 1:
                raise ConfigError("escalation: after_minutes must be at least 1")
            targets = tuple(str(name) for name in (raw_escalation.get("send_to") or []))
            if not targets:
                raise ConfigError("escalation: send_to names no channel, so nothing would be escalated to")
            unknown = [name for name in targets if name not in channels]
            if unknown:
                raise ConfigError(f"escalation: send_to has unconfigured channel(s) {', '.join(unknown)}")
            escalation = Escalation(
                after_minutes=after,
                send_to=targets,
                levels=tuple(str(level).lower() for level in (raw_escalation.get("levels") or [])),
            )

        return cls(
            sources=sources,
            channels=channels,
            routes=routes and tuple(routes) or (),
            pipeline=pipeline,
            card_actions=card_actions,
            escalation=escalation,
        )


def _fingerprint_vocabulary(src: Source, pipeline: tuple[PipelineStage, ...]) -> set[str] | None:
    """Every name `fingerprint_fields` could legitimately mean for this door.

    The union of ALL the door's templates, never one of them: a door's templates
    are ordered ALTERNATIVES, and a field only one of them extracts can still
    drive identity (events read by the others simply miss it, which is
    documented behaviour). Checking against a single template would refuse
    honest config, and a validator that refuses honest config is worse than the
    typo it was written to catch.

    Returns None — "cannot be enumerated, do not judge" — when a stage before
    the fingerprint may invent field names: an `http` brain answers with fields
    only it knows, and a plugin processor can do anything. `set` is the
    exception, because its additions are written down right there in the config.

    The fingerprint is taken by the dedup stage, or at record time when there is
    no dedup stage, so only the walk up to that point can widen the vocabulary.
    """
    names = set(BUILTIN_EXTRACTED_NAMES)
    for template in src.templates:
        names |= set(template.fields)
    for stage in pipeline:
        if stage.type == "set":
            changes = stage.options.get("set") or {}
            declared = changes.get("fields") if isinstance(changes, dict) else None
            if isinstance(declared, dict):
                names |= {str(key) for key in declared}
        elif stage.type not in _FIELD_PRESERVING_PROCESSORS:
            return None
        if stage.type == "dedup":
            break
    return names


def _condition_matches(condition: Any, actual: str) -> bool:
    """One `when` entry against one extracted value.

    Three forms, no expression language on purpose:
      "high"              — exact match
      ["high", "medium"]  — membership
      {"contains": "db"}  — substring
    """
    if isinstance(condition, list):
        return actual in [str(v) for v in condition]
    if isinstance(condition, dict) and "contains" in condition:
        return str(condition["contains"]) in actual
    return actual == str(condition)


def match_routes(cfg: Config, source_name: str, context: dict[str, str]) -> tuple[list[str], list[dict[str, Any]]]:
    """Walk routes (priority order) and collect target channels.

    Returns (ordered unique channel names, per-route trace steps) — the trace
    is the WHY, kept alongside the decision, matching the family doctrine that
    an event you cannot explain is an event you cannot trust.
    """
    matched: list[str] = []
    steps: list[dict[str, Any]] = []
    for route in cfg.routes:
        if route.source != "*" and route.source != source_name:
            continue
        misses = [key for key, cond in route.when.items() if not _condition_matches(cond, context.get(key, ""))]
        if misses:
            steps.append({"route": route.name, "matched": False, "missed_on": misses})
            continue
        steps.append({"route": route.name, "matched": True, "send_to": list(route.send_to), "stop": route.stop})
        for channel_name in route.send_to:
            if channel_name not in matched:
                matched.append(channel_name)
        if route.stop:
            break
    return matched, steps
