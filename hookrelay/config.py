"""Routing configuration: sources, channels, routes — loaded from one YAML file.

A source is an inbound door (who may knock, how to read what they say).
A channel is an outbound pipe (where messages go, how fast they may flow).
A route is the sentence connecting them: "events from X that look like Y go
to channels Z".

Secrets never sit in the YAML: any value written as ${NAME} resolves from the
environment at load time, so the file itself is safe to commit.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

import yaml

_ENV_REF = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")

CHANNEL_TYPES = ("feishu", "dingtalk", "wecom", "generic")


class ConfigError(ValueError):
    """A configuration mistake worth stopping the process for."""


def _resolve(value: Any) -> Any:
    if isinstance(value, str):
        match = _ENV_REF.match(value.strip())
        if match:
            return os.environ.get(match.group(1), "")
    return value


@dataclass(frozen=True, slots=True)
class Source:
    name: str
    secret: str  # empty = unsigned source (document why before doing that)
    title: str
    body: str
    level: str
    level_map: dict[str, str] = field(default_factory=dict)
    fields: dict[str, str] = field(default_factory=dict)
    # Which extracted keys form the duplicate fingerprint. Empty = title+body.
    fingerprint_fields: tuple[str, ...] = ()
    dedup_window_seconds: int = 120


@dataclass(frozen=True, slots=True)
class Channel:
    name: str
    type: str
    url: str
    secret: str = ""
    # 0 = unlimited. Enforced at delivery time by deferring, never dropping.
    max_per_minute: int = 0


@dataclass(frozen=True, slots=True)
class Route:
    name: str
    source: str  # "*" matches every source
    send_to: tuple[str, ...]
    when: dict[str, Any] = field(default_factory=dict)
    priority: int = 0
    stop: bool = False


@dataclass(frozen=True, slots=True)
class Config:
    sources: dict[str, Source]
    channels: dict[str, Channel]
    routes: tuple[Route, ...]  # kept sorted by priority, highest first

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
                level_map={str(k).lower(): str(v) for k, v in (item.get("level_map") or {}).items()},
                fields={str(k): str(v) for k, v in (item.get("fields") or {}).items()},
                fingerprint_fields=tuple(item.get("fingerprint_fields") or ()),
                dedup_window_seconds=int(item.get("dedup_window_seconds", 120)),
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
            )
            if ch.type not in CHANNEL_TYPES:
                raise ConfigError(f"channel {ch.name}: unknown type {ch.type!r} (known: {CHANNEL_TYPES})")
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
        return cls(sources=sources, channels=channels, routes=tuple(routes))


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
