"""The delivery worker: due rows out of the outbox, with backoff and limits.

process_due() is a pure step (given a clock, drain what is due once) so tests
drive it deterministically; the loop is just that step on a timer.

Concurrency shape — the one decision worth stating: due rows are grouped BY
CHANNEL, groups run concurrently, and each group runs sequentially. Both halves
are load-bearing:

  across channels, parallel — a channel that hangs for its full timeout used to
    head-of-line block every delivery behind it in the batch, including healthy
    ones to unrelated channels.
  within a channel, serial — the per-minute rate limit counts what has been
    SENT, so two concurrent sends to one channel could both read "under the
    limit" and both go. Serial per channel keeps the limit honest.

Rate limits and open breakers DEFER: choosing not to send right now is
scheduling, not failure, and must never burn an attempt.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from hookrelay import channels, metrics
from hookrelay.alarm import SelfAlarm
from hookrelay.breaker import CircuitBreaker
from hookrelay.config import Config
from hookrelay.settings import Settings
from hookrelay.store import Store

_RATE_DEFER_SECONDS = 10
_BREAKER_DEFER_SECONDS = 15
_BACKOFF_BASE_SECONDS = 30
_BACKOFF_CAP_SECONDS = 600


def backoff_delay(attempts: int) -> float:
    return float(min(_BACKOFF_CAP_SECONDS, _BACKOFF_BASE_SECONDS * (2 ** max(0, attempts - 1))))


async def process_due(
    store: Store,
    cfg: Config,
    settings: Settings,
    client: httpx.AsyncClient,
    now: float,
    alarm: SelfAlarm | None = None,
    breaker: CircuitBreaker | None = None,
) -> int:
    rows = await store.due_deliveries(now)
    if not rows:
        return 0

    by_channel: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_channel.setdefault(str(row["channel"]), []).append(row)

    counts = await asyncio.gather(
        *(_drain_channel(store, cfg, settings, client, now, group, alarm, breaker) for group in by_channel.values())
    )
    return sum(counts)


async def _drain_channel(
    store: Store,
    cfg: Config,
    settings: Settings,
    client: httpx.AsyncClient,
    now: float,
    rows: list[dict[str, Any]],
    alarm: SelfAlarm | None,
    breaker: CircuitBreaker | None,
) -> int:
    processed = 0
    for row in rows:
        channel = cfg.channels.get(row["channel"])
        if channel is None:
            # Config changed underneath a queued row: dead-letter it with the
            # reason instead of retrying into a void forever.
            await store.mark_failed(row["id"], row["attempts"] + 1, "channel no longer configured", None)
            metrics.record_delivery(row["channel"], "dead")
            if alarm is not None:
                await alarm.dead_letter(
                    client,
                    channel=row["channel"],
                    event_id=row["event_id"],
                    error="channel no longer configured",
                    now=now,
                )
            processed += 1
            continue

        if breaker is not None and not breaker.allows(channel.name, now):
            await store.defer_delivery(row["id"], now + _BREAKER_DEFER_SECONDS)
            metrics.record_delivery(channel.name, "deferred")
            continue

        if channel.max_per_minute > 0:
            sent_last_minute = await store.sent_count_since(channel.name, now - 60)
            if sent_last_minute >= channel.max_per_minute:
                await store.defer_delivery(row["id"], now + _RATE_DEFER_SECONDS)
                metrics.record_delivery(channel.name, "deferred")
                continue

        message = {
            "event_id": row["event_id"],
            "source": row["source"],
            "title": row["title"],
            "body": row["body"],
            "level": row["level"],
            "fields": json.loads(row["fields_json"] or "{}"),
            "received_at": row["received_at"],
            # The original inbound payload, for raw-mode channels. Normalized
            # channels never serialize it (generic strips it before signing).
            "payload": json.loads(row["payload_json"] or "null"),
            # At-least-once made safe for the receiver: stable across retries of
            # THIS row, so a re-send after a crash between send and bookkeeping
            # is recognisable as the same delivery, not a second alert.
            "_idempotency_key": f"{row['event_id']}:{row['channel']}",
            # Quotable identity for a brain that will send work back through
            # another door: the return event can then be linked to this one.
            "_correlation_id": f"hr-{row['event_id']}",
        }
        ok, detail = await channels.send(client, channel, message)
        processed += 1
        if ok:
            await store.mark_sent(row["id"], now)
            metrics.record_delivery(channel.name, "sent")
            if breaker is not None:
                breaker.record_success(channel.name)
        else:
            attempts = row["attempts"] + 1
            next_at = None if attempts >= settings.max_attempts else now + backoff_delay(attempts)
            await store.mark_failed(row["id"], attempts, detail, next_at)
            metrics.record_delivery(channel.name, "dead" if next_at is None else "failed")
            if breaker is not None:
                breaker.record_failure(channel.name, now)
            if next_at is None and alarm is not None:
                await alarm.dead_letter(client, channel=channel.name, event_id=row["event_id"], error=detail, now=now)
    return processed
