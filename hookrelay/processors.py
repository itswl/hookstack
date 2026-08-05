"""Processors: the configurable middle of the pipeline.

A processor sees a mutable event context and returns a verdict:
    ("pass", None)        — continue down the pipeline (mutations kept)
    ("skip", skip_code)   — stop; the event is recorded with that named code

Built-ins:
    dedup    — fingerprint window (the original gate ①)
    silence  — operator quiet switch (gate ②)
    routes   — rule matching; fills ctx.channels (gate ③)
    set      — static mutation (level/title/body/fields), optionally guarded
    filter   — drop events matching conditions, with a named skip_code
    http     — POST the event to an EXTERNAL processor (a brain like
               WebhookWise, a scorer, anything speaking the tiny contract)
               and apply its verdict/enrichment

The pipeline ORDER is config, so "dedup on the raw title, then let the brain
rewrite it" and "let the brain rewrite it, then dedup on the rewrite" are both
one line apart — order sensitivity is the feature, not a trap.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from hookrelay import registry
from hookrelay.config import Config, Source, _condition_matches, match_routes
from hookrelay.extract import fingerprint
from hookrelay.settings import Settings
from hookrelay.store import Store


@dataclass
class EventContext:
    """Mutable working state for one event's walk down the pipeline."""

    source: Source
    payload: Any
    extracted: dict[str, Any]
    now: float
    steps: list[dict[str, Any]] = field(default_factory=list)
    channels: list[str] = field(default_factory=list)
    fingerprint: str | None = None  # set by dedup; computed at record time otherwise

    def routing_context(self) -> dict[str, str]:
        return {
            "source": self.source.name,
            "level": self.extracted["level"],
            "title": self.extracted["title"],
            **self.extracted["fields"],
        }


@dataclass(frozen=True)
class Runtime:
    """What processors may touch besides the event itself."""

    store: Store
    config: Config
    settings: Settings | None  # None when embedded/tested outside the app
    http_client: httpx.AsyncClient | None  # None only in unit tests that stub


Verdict = tuple[str, Any]
PASS: Verdict = ("pass", None)


@registry.processor("dedup")
class DedupProcessor:
    async def run(self, rt: Runtime, ctx: EventContext, options: dict[str, Any]) -> Verdict:
        fp = fingerprint(ctx.source, ctx.extracted)
        ctx.fingerprint = fp
        duplicate = await rt.store.recent_duplicate(fp, ctx.source.dedup_window_seconds, ctx.now)
        if duplicate is not None:
            ctx.steps.append(
                {
                    "gate": "dedup",
                    "result": "duplicate",
                    "first_event_id": duplicate["id"],
                    "seconds_ago": int(ctx.now - float(duplicate["received_at"])),
                }
            )
            return ("skip", "duplicate")
        ctx.steps.append({"gate": "dedup", "result": "pass"})
        return PASS


@registry.processor("silence")
class SilenceProcessor:
    async def run(self, rt: Runtime, ctx: EventContext, options: dict[str, Any]) -> Verdict:
        silence = await rt.store.active_silence(ctx.source.name, ctx.now)
        if silence is not None:
            ctx.steps.append(
                {"gate": "silence", "result": "silenced", "silence_id": silence["id"], "note": silence["note"]}
            )
            return ("skip", "silenced")
        ctx.steps.append({"gate": "silence", "result": "pass"})
        return PASS


@registry.processor("routes")
class RoutesProcessor:
    async def run(self, rt: Runtime, ctx: EventContext, options: dict[str, Any]) -> Verdict:
        channels, route_steps = match_routes(rt.config, ctx.source.name, ctx.routing_context())
        ctx.steps.append({"gate": "routes", "considered": route_steps, "matched_channels": channels})
        ctx.channels = channels
        return PASS


@registry.processor("set")
class SetProcessor:
    """Static enrichment: {set: {level: high, fields: {team: db}}, when: {...}}."""

    async def run(self, rt: Runtime, ctx: EventContext, options: dict[str, Any]) -> Verdict:
        when: dict[str, Any] = options.get("when") or {}
        context = ctx.routing_context()
        if when and any(not _condition_matches(cond, context.get(key, "")) for key, cond in when.items()):
            ctx.steps.append({"gate": options["_name"], "result": "not_applied"})
            return PASS
        changes: dict[str, Any] = options.get("set") or {}
        for key in ("title", "body", "level"):
            if key in changes:
                ctx.extracted[key] = str(changes[key])
        if isinstance(changes.get("fields"), dict):
            ctx.extracted["fields"].update({str(k): str(v) for k, v in changes["fields"].items()})
        ctx.steps.append({"gate": options["_name"], "result": "applied", "set": sorted(changes.keys())})
        return PASS


@registry.processor("filter")
class FilterProcessor:
    """Named drop: {when: {level: [low]}, skip_code: low_filtered}."""

    async def run(self, rt: Runtime, ctx: EventContext, options: dict[str, Any]) -> Verdict:
        when: dict[str, Any] = options.get("when") or {}
        context = ctx.routing_context()
        if when and all(_condition_matches(cond, context.get(key, "")) for key, cond in when.items()):
            code = str(options.get("skip_code") or "filtered")
            ctx.steps.append({"gate": options["_name"], "result": "dropped", "skip_code": code})
            return ("skip", code)
        ctx.steps.append({"gate": options["_name"], "result": "pass"})
        return PASS


@registry.processor("http")
class HttpProcessor:
    """Hand the event to an external brain, apply what it says.

    Request:  POST {url} with {"source", "event": {title,body,level,fields},
              "received_at"} plus configured headers.
    Response: {"action": "pass"|"drop", "skip_code"?: str,
               "set"?: {"title"/"body"/"level"/"fields": {...}}}

    on_error (timeout / non-2xx / bad JSON) is a POLICY, chosen per stage:
      pass — fail open, the step records the error, the event continues
      drop — fail closed with skip_code processor_error
    A router must never invent a third behaviour on the fly.
    """

    async def run(self, rt: Runtime, ctx: EventContext, options: dict[str, Any]) -> Verdict:
        name = options["_name"]
        on_error = str(options.get("on_error") or "pass")
        request_body = {
            "source": ctx.source.name,
            "event": {
                "title": ctx.extracted["title"],
                "body": ctx.extracted["body"],
                "level": ctx.extracted["level"],
                "fields": ctx.extracted["fields"],
            },
            "received_at": ctx.now,
        }
        try:
            assert rt.http_client is not None, "http processor needs a client"
            response = await rt.http_client.post(
                str(options["url"]),
                json=request_body,
                headers={str(k): str(v) for k, v in (options.get("headers") or {}).items()},
                timeout=float(options.get("timeout_seconds", 3.0)),
            )
            response.raise_for_status()
            data = response.json()
        except Exception as error:  # noqa: BLE001 — every failure lands in the same policy
            detail = f"{error.__class__.__name__}: {error}"
            if on_error == "drop":
                ctx.steps.append({"gate": name, "result": "error_drop", "error": detail[:200]})
                return ("skip", "processor_error")
            ctx.steps.append({"gate": name, "result": "error_pass", "error": detail[:200]})
            return PASS

        changes = data.get("set") or {}
        for key in ("title", "body", "level"):
            if key in changes:
                ctx.extracted[key] = str(changes[key])
        if isinstance(changes.get("fields"), dict):
            ctx.extracted["fields"].update({str(k): str(v) for k, v in changes["fields"].items()})

        if data.get("action") == "drop":
            code = str(data.get("skip_code") or "processor_drop")
            ctx.steps.append({"gate": name, "result": "dropped", "skip_code": code})
            return ("skip", code)
        ctx.steps.append({"gate": name, "result": "pass", "applied": sorted(changes.keys())})
        return PASS
