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
from dataclasses import dataclass, field
from typing import Any

import yaml

from hookrelay import registry

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


@dataclass(frozen=True, slots=True)
class Source:
    name: str
    secret: str  # empty = unsigned source (document why before doing that)
    title: str
    body: str
    level: str
    adapter: str = "default"
    level_map: dict[str, str] = field(default_factory=dict)
    fields: dict[str, str] = field(default_factory=dict)
    # Which extracted keys form the duplicate fingerprint. Empty = title+body.
    fingerprint_fields: tuple[str, ...] = ()
    dedup_window_seconds: int = 120
    # Storm fuse (volume, not content — see hookrelay/fuse.py). 0 = no fuse.
    # Mandatory reading: a brain-paired deploy may rely on the brain's own
    # backpressure, but a relay in front of something WITHOUT backpressure
    # (e.g. WebhookWise-lite) must carry its own fuse.
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
    # receiver's dialect — e.g. X-Webhook-Signature to feed WebhookWise.
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


@dataclass(frozen=True, slots=True)
class Config:
    sources: dict[str, Source]
    channels: dict[str, Channel]
    routes: tuple[Route, ...]  # kept sorted by priority, highest first
    pipeline: tuple[PipelineStage, ...] = DEFAULT_PIPELINE

    @classmethod
    def from_file(cls, path: str) -> Config:
        with open(path, encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Config:
        sources: dict[str, Source] = {}
        for item in raw.get("sources") or []:
            src = Source(
                name=str(item["name"]),
                secret=str(_resolve(item.get("secret", "")) or ""),
                title=str(item.get("title", "{title}")),
                body=str(item.get("body", "{body}")),
                level=str(item.get("level", "")),
                adapter=str(item.get("adapter", "default")),
                level_map={str(k).lower(): str(v) for k, v in (item.get("level_map") or {}).items()},
                fields={str(k): str(v) for k, v in (item.get("fields") or {}).items()},
                fingerprint_fields=tuple(item.get("fingerprint_fields") or ()),
                dedup_window_seconds=int(item.get("dedup_window_seconds", 120)),
                storm_threshold=max(0, int(item.get("storm_threshold", 0))),
                storm_window_seconds=max(1, int(item.get("storm_window_seconds", 60))),
                require_timestamp=bool(item.get("require_timestamp", False)),
                max_skew_seconds=max(1, int(item.get("max_skew_seconds", 300))),
                options=_resolve_deep(dict(item.get("options") or {})),
            )
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

        return cls(sources=sources, channels=channels, routes=routes and tuple(routes) or (), pipeline=pipeline)


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
