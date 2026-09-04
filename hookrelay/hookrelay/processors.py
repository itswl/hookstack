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
               hookjudge, a scorer, anything speaking the tiny contract)
               and apply its verdict/enrichment

The pipeline ORDER is config, so "dedup on the raw title, then let the brain
rewrite it" and "let the brain rewrite it, then dedup on the rewrite" are both
one line apart — order sensitivity is the feature, not a trap.

A DRY RUN (Runtime.dry_run, from /explain) walks these same stages with this
same code — one walk, or the explanation drifts from the behaviour it explains
— so a stage with a SIDE EFFECT must check the flag and report instead of act.
The `http` stage is the one built-in that has one, and it used to POST during
an explain: a route whose docstring promised the answer could never leave the
process was handing a real payload to a real external service (and, for a
per-call brain, a real bill) for a question about a payload nobody sent. A
plugin processor that touches anything outside the context owes the same check.
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
    # Set when a brain quoted our correlation id back: this event is the RETURN
    # half of a round trip, and the ledger can gather it under the original.
    correlation_id: str | None = None

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
    # Set by /explain: the caller is asking what WOULD happen, so a stage with
    # a side effect must report it rather than perform it. Threaded here rather
    # than by duplicating the walk — a second walk that "does the same minus
    # the sends" is a walk that quietly stops matching the first one.
    dry_run: bool = False


Verdict = tuple[str, Any]
PASS: Verdict = ("pass", None)

# An inline processor holds the sender's connection open. Past this, the right
# answer is a different topology, not a bigger number.
MAX_INLINE_TIMEOUT_SECONDS = 10.0


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

    INLINE MEANS FAST. This stage blocks the inbound HTTP request, so a slow
    brain here makes the SENDER time out and retry — you get duplicate alerts
    while the first copy is still being analysed. The timeout is capped
    (MAX_INLINE_TIMEOUT_SECONDS, refused at config load above it): anything
    slower belongs in the async topology — deliver TO the brain as a channel
    and let it re-enter through its own door, which is how a 47-second AI
    analysis is wired in production.

    A DRY RUN never calls the brain — see the module docstring. It still says
    where the call would have gone, because showing the walk is the whole point
    of an explain, and "the verdict comes from here" is part of the walk.

    `when` puts the stage on ONE LANE — {source: probe-notify}, {level: [high]},
    any routing key, same matcher as `set` and `filter`. It is what makes an
    inline node PLACEABLE: without it the stage fires on every event, so the
    only way to scope a decider was to teach it every source name in the config
    — which an outside node cannot know, and which makes each new lane an edit
    to somebody else's service. Checked before the dry-run branch on purpose: an
    explain should say "not_applied" for a lane this stage does not cover, not
    claim it would post there.
    """

    async def run(self, rt: Runtime, ctx: EventContext, options: dict[str, Any]) -> Verdict:
        name = options["_name"]
        when: dict[str, Any] = options.get("when") or {}
        context = ctx.routing_context()
        if when and any(not _condition_matches(cond, context.get(key, "")) for key, cond in when.items()):
            ctx.steps.append({"gate": name, "result": "not_applied"})
            return PASS
        if rt.dry_run:
            # The one thing this stage cannot honestly show is the verdict it
            # never asked for, so it says that too — an operator reading the
            # steps below must not take them for what the brain would have made
            # of this event.
            ctx.steps.append(
                {
                    "gate": name,
                    "result": "would_post",
                    "url": str(options["url"]),
                    "note": "dry run — the brain was not called, so nothing below reflects its verdict",
                }
            )
            return PASS
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
            if rt.http_client is None:
                raise RuntimeError("http processor needs a client")
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
