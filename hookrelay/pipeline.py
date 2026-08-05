"""The decision pipeline: named gates, walked in a fixed order.

    ① duplicate — same fingerprint already seen inside the window
    ② silenced  — an operator asked for quiet (source-scoped or global)
    ③ no_route  — no route claims this event

Order is meaning: dedup before silence so a storm of repeats never floods the
silence check; both before routing so a skipped event costs nothing downstream.
Every event — forwarded or not — leaves exactly one decision row with the
ordered steps, because "why didn't it arrive?" is the first question anyone
asks a router, and the answer must not require re-deriving state from logs.
"""

from __future__ import annotations

import json
from typing import Any

from hookrelay.config import Config, Source, match_routes
from hookrelay.extract import extract_event, fingerprint
from hookrelay.store import Store


async def handle_hook(store: Store, cfg: Config, source: Source, payload: Any, now: float) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    extracted = extract_event(source, payload)
    fp = fingerprint(source, extracted)

    # ① duplicate — checked BEFORE the event row is written, so repeats are
    # recorded as skips against the original, not as a second identity.
    duplicate = await store.recent_duplicate(fp, source.dedup_window_seconds, now)
    if duplicate is not None:
        steps.append(
            {
                "gate": "dedup",
                "result": "duplicate",
                "first_event_id": duplicate["id"],
                "seconds_ago": int(now - float(duplicate["received_at"])),
            }
        )
        event_id = await _record(store, source, fp, extracted, payload, now, "skipped", "duplicate", [], steps)
        return {"event_id": event_id, "outcome": "skipped", "skip_code": "duplicate", "steps": steps}
    steps.append({"gate": "dedup", "result": "pass"})

    # ② silence — before routing so a silenced source costs no rule walk.
    silence = await store.active_silence(source.name, now)
    if silence is not None:
        steps.append({"gate": "silence", "result": "silenced", "silence_id": silence["id"], "note": silence["note"]})
        event_id = await _record(store, source, fp, extracted, payload, now, "skipped", "silenced", [], steps)
        return {"event_id": event_id, "outcome": "skipped", "skip_code": "silenced", "steps": steps}
    steps.append({"gate": "silence", "result": "pass"})

    # ③ routing — the trace carries every route considered and why it
    # matched or missed, not just the winners.
    context = {"source": source.name, "level": extracted["level"], "title": extracted["title"], **extracted["fields"]}
    channels, route_steps = match_routes(cfg, source.name, context)
    steps.append({"gate": "routes", "considered": route_steps, "matched_channels": channels})
    if not channels:
        event_id = await _record(store, source, fp, extracted, payload, now, "skipped", "no_route", [], steps)
        return {"event_id": event_id, "outcome": "skipped", "skip_code": "no_route", "steps": steps}

    event_id = await _record(store, source, fp, extracted, payload, now, "routed", None, channels, steps)
    for channel_name in channels:
        await store.enqueue_delivery(event_id, channel_name, now)
    return {"event_id": event_id, "outcome": "routed", "channels": channels, "steps": steps}


async def _record(
    store: Store,
    source: Source,
    fp: str,
    extracted: dict[str, Any],
    payload: Any,
    now: float,
    outcome: str,
    skip_code: str | None,
    channels: list[str],
    steps: list[dict[str, Any]],
) -> int:
    payload_json = json.dumps(payload, ensure_ascii=False)
    # The raw payload is forensic context, not the working copy — cap it so a
    # hostile 200KB body cannot bloat every row it touches.
    if len(payload_json) > 32_768:
        payload_json = json.dumps({"_truncated": True, "_bytes": len(payload_json)})
    event_id = await store.insert_event(source.name, fp, extracted, payload_json, now)
    await store.insert_decision(event_id, outcome, skip_code, channels, steps)
    return event_id
