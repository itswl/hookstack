"""The pipeline runner: walk the configured stages, record one decision.

The stages themselves live in processors.py (or in plugins); this file only
owns the walk and the invariant the whole project stands on: every event —
routed or skipped — leaves exactly ONE decision row carrying the ordered
steps, because "why didn't it arrive?" is the first question anyone asks a
router, and the answer must not require re-deriving state from logs.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from hookrelay import metrics, registry
from hookrelay.config import Config, Source
from hookrelay.extract import extract_event, fingerprint
from hookrelay.processors import EventContext, Runtime
from hookrelay.settings import Settings
from hookrelay.store import Store


async def handle_hook(
    store: Store,
    cfg: Config,
    source: Source,
    payload: Any,
    now: float,
    *,
    settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
    extracted: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one event through the configured pipeline.

    `extracted` is normally produced by the source's adapter in the HTTP
    layer; direct callers (tests, embedding) may omit it and get the default
    template extraction.
    """
    ctx = EventContext(
        source=source,
        payload=payload,
        extracted=extracted if extracted is not None else extract_event(source, payload),
        now=now,
    )
    rt = Runtime(store=store, config=cfg, settings=settings, http_client=client)

    for stage in cfg.pipeline:
        processor = registry.PROCESSORS[stage.type]
        options = dict(stage.options)
        options["_name"] = stage.name
        verdict, detail = await processor.run(rt, ctx, options)
        if verdict == "skip":
            event_id = await _record(store, ctx, "skipped", str(detail), [])
            return {"event_id": event_id, "outcome": "skipped", "skip_code": str(detail), "steps": ctx.steps}

    if not ctx.channels:
        event_id = await _record(store, ctx, "skipped", "no_route", [])
        return {"event_id": event_id, "outcome": "skipped", "skip_code": "no_route", "steps": ctx.steps}

    event_id = await _record(store, ctx, "routed", None, ctx.channels)
    for channel_name in ctx.channels:
        await store.enqueue_delivery(event_id, channel_name, now)
    return {"event_id": event_id, "outcome": "routed", "channels": ctx.channels, "steps": ctx.steps}


async def record_storm_suppressed(
    store: Store, source: Source, payload: Any, now: float, count: int, threshold: int
) -> int:
    """A fused event still gets an account — the storm is exactly when you most
    need to know what arrived. It walks no pipeline and reaches no channel."""
    ctx = EventContext(source=source, payload=payload, extracted=extract_event(source, payload), now=now)
    ctx.steps.append({"gate": "storm_fuse", "result": "suppressed", "window_count": count, "threshold": threshold})
    return await _record(store, ctx, "skipped", "storm_suppressed", [])


async def _record(store: Store, ctx: EventContext, outcome: str, skip_code: str | None, channels: list[str]) -> int:
    # Stored WHOLE: with raw-passthrough channels the payload IS the working
    # copy (the exact thing a downstream brain receives), so truncating here
    # would silently corrupt deliveries. Size is already bounded upstream by
    # the ingress body cap (413 at the door), and retention purges old rows.
    payload_json = json.dumps(ctx.payload, ensure_ascii=False)
    fp = ctx.fingerprint or fingerprint(ctx.source, ctx.extracted)
    event_id = await store.insert_event(ctx.source.name, fp, ctx.extracted, payload_json, ctx.now)
    await store.insert_decision(event_id, outcome, skip_code, channels, ctx.steps)
    metrics.record_event(ctx.source.name, skip_code or outcome)
    return event_id
