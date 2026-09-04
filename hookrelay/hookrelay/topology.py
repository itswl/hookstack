"""The whole shape this config describes, computed without calling anything.

`/explain/{source}` answers "where does THIS event go". This answers the other
half — "what does my topology look like" — and it is the only thing in the pipe
that can be read BEFORE a route change instead of after one.

It is pure: Config in, dict out, no store, no network, no clock. That is what
makes it safe to serve on a live deployment, and it is also the boundary that
keeps it honest — a topology that could call something would eventually start
reporting what happened rather than what is configured.

WHAT IT DELIBERATELY DOES NOT PRINT: channel URLs, past the host. A webhook URL
IS its credential — a Lark bot URL carries the token in its path — and unlike
`GET /config`, which serves the file where secrets are still `${REFS}`, this
renders the RESOLVED config where those refs have already become real values.
`scheme://host:port` answers the only question a topology asks of an edge (which
node does this point at) and carries no token. The rest is in the config file,
which is the right place to need credentials to read.

The warnings are structural facts, never guesses. This module cannot know which
of your doors is a RETURN door — that mapping lives in the node's env, not in
this config — so it reports the shape and names the hazard rather than asserting
a bug. `wildcard_fallthrough` is the one worth reading twice: it is the failure
every config in this family guards against by hand, four separate comments deep.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from hookrelay.config import SYNTHETIC_SOURCES, Config, Route


def _target(url: str) -> str:
    """`scheme://host:port`, never the path, query or userinfo.

    `urlsplit().hostname` drops userinfo for free, which is the half of this
    that is easy to forget: `https://user:pass@host/path` leaks in `netloc` and
    not in `hostname`.
    """
    parts = urlsplit(url or "")
    if not parts.hostname:
        return ""
    return f"{parts.scheme}://{parts.hostname}:{parts.port}" if parts.port else f"{parts.scheme}://{parts.hostname}"


def _candidates(config: Config, source_name: str) -> list[Route]:
    """Every route that COULD match this source, in the order the walk sees them.

    `config.routes` is already sorted by descending priority, so this preserves
    walk order by filtering rather than re-sorting. `when` is not evaluated:
    whether a condition holds depends on an event, and this function is about
    the config alone.
    """
    return [route for route in config.routes if route.source in ("*", source_name)]


def _walk_is_guaranteed_terminal(candidates: list[Route]) -> bool:
    """True when SOME route on this walk always stops it.

    Only an unconditional `stop: true` qualifies. A terminal route carrying a
    `when` stops the walk for the events it matches and no others, which is
    exactly the case where a fallthrough hides until the day an event misses it.
    """
    return any(route.stop and not route.when for route in candidates)


def render(config: Config) -> dict[str, Any]:
    """Doors, stages, exits and the warnings the shape itself implies."""
    fed_by: dict[str, list[str]] = {name: [] for name in config.channels}
    for route in config.routes:
        for channel_name in route.send_to:
            if channel_name in fed_by:
                fed_by[channel_name].append(route.name)

    # A card button is the SECOND way something leaves this pipe, and it does
    # not walk the route table at all. Counting only routes made the two
    # feedback channels of a real deployment look dead, which is the way a
    # warning stops being read — so the button path is a first-class edge here,
    # kept separate from routes because it is triggered by a person, not by an
    # event, and a reader deciding whether a lane is live needs to know which.
    pressed_by: dict[str, list[str]] = {name: [] for name in config.channels}
    for kind, action in config.card_actions.items():
        target = getattr(action, "forward_to", "") or ""
        if target in pressed_by:
            pressed_by[target].append(kind)

    doors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    # The pipe's own doors first. They have no `sources` entry — nothing posts to
    # them from outside — so a graph built only from config would omit the routes
    # that carry a card press and an absence alarm, and the absence route is
    # precisely the one somebody checks after a quiet afternoon.
    synthetic = [s for s in SYNTHETIC_SOURCES if any(r.source == s for r in config.routes)]
    for name in synthetic:
        candidates = _candidates(config, name)
        doors.append(
            {
                "name": name,
                "signed": None,  # not a door anything can post to
                "adapter": "(the pipe itself)",
                "routes": [
                    {
                        "name": r.name,
                        "matches": r.source,
                        "when": r.when,
                        "send_to": list(r.send_to),
                        "priority": r.priority,
                        "stop": r.stop,
                    }
                    for r in candidates
                ],
                "walk_always_stops": _walk_is_guaranteed_terminal(candidates),
            }
        )
        if not candidates:
            warnings.append(
                {
                    "kind": "unreachable_door",
                    "door": name,
                    "detail": f"the pipe can raise {name} events and no route carries them — they would end no_route",
                }
            )

    for name, source in config.sources.items():
        candidates = _candidates(config, name)
        doors.append(
            {
                "name": name,
                "signed": bool(source.secret),
                "adapter": source.adapter,
                "routes": [
                    {
                        "name": route.name,
                        "matches": route.source,
                        "when": route.when,
                        "send_to": list(route.send_to),
                        "priority": route.priority,
                        "stop": route.stop,
                    }
                    for route in candidates
                ],
                "walk_always_stops": _walk_is_guaranteed_terminal(candidates),
            }
        )

        if not candidates:
            warnings.append(
                {
                    "kind": "unreachable_door",
                    "door": name,
                    "detail": "no route can match this source — every event through this door ends no_route",
                }
            )
            continue

        # The hazard, stated structurally. Walk in priority order and stop at
        # the first route that is guaranteed to end it; a wildcard reached
        # before that point will accumulate for this door.
        for route in candidates:
            if route.source == "*":
                warnings.append(
                    {
                        "kind": "wildcard_fallthrough",
                        "door": name,
                        "route": route.name,
                        "send_to": list(route.send_to),
                        "detail": (
                            f"events from {name} can accumulate the wildcard route {route.name!r} "
                            f"before any route is guaranteed to stop the walk. Intended for a front "
                            f"door; if {name} is a node's RETURN door, this is the loop that feeds a "
                            f"brain its own output — give it a higher-priority route with stop: true"
                        ),
                    }
                )
                break
            if route.stop and not route.when:
                break

    for name in config.channels:
        if not fed_by[name] and not pressed_by[name]:
            warnings.append(
                {
                    "kind": "starved_exit",
                    "exit": name,
                    "detail": "no route and no card button sends here — this node is configured and unreachable",
                }
            )

    return {
        "doors": doors,
        "pipeline": [
            {"name": stage.name, "type": stage.type, "scoped_to": stage.options.get("when") or {}}
            for stage in config.pipeline
        ],
        "exits": [
            {
                "name": name,
                "type": channel.type,
                "target": _target(channel.url),
                "signed": bool(channel.secret),
                "payload": str(channel.options.get("payload") or "normalized"),
                "fed_by": fed_by[name],
                "pressed_by": pressed_by[name],
            }
            for name, channel in config.channels.items()
        ],
        "warnings": warnings,
    }
