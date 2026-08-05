"""The delivery worker: due rows out of the outbox, with backoff and limits.

process_due() is a pure step (given a clock, drain what is due once) so tests
drive it deterministically; the loop is just that step on a timer. Rate limits
DEFER — a limited delivery is pushed a few seconds, never dropped and never
counted as a failure, because "we chose to slow down" is not an error.
"""

from __future__ import annotations

import json

import httpx

from hookrelay import channels
from hookrelay.config import Config
from hookrelay.settings import Settings
from hookrelay.store import Store

_RATE_DEFER_SECONDS = 10
_BACKOFF_BASE_SECONDS = 30
_BACKOFF_CAP_SECONDS = 600


def backoff_delay(attempts: int) -> float:
    return float(min(_BACKOFF_CAP_SECONDS, _BACKOFF_BASE_SECONDS * (2 ** max(0, attempts - 1))))


async def process_due(store: Store, cfg: Config, settings: Settings, client: httpx.AsyncClient, now: float) -> int:
    processed = 0
    for row in await store.due_deliveries(now):
        channel = cfg.channels.get(row["channel"])
        if channel is None:
            # Config changed underneath a queued row: dead-letter it with the
            # reason instead of retrying into a void forever.
            await store.mark_failed(row["id"], row["attempts"] + 1, "channel no longer configured", None)
            processed += 1
            continue

        if channel.max_per_minute > 0:
            sent_last_minute = await store.sent_count_since(channel.name, now - 60)
            if sent_last_minute >= channel.max_per_minute:
                await store.defer_delivery(row["id"], now + _RATE_DEFER_SECONDS)
                continue

        message = {
            "event_id": row["event_id"],
            "source": row["source"],
            "title": row["title"],
            "body": row["body"],
            "level": row["level"],
            "fields": json.loads(row["fields_json"] or "{}"),
            "received_at": row["received_at"],
        }
        ok, detail = await channels.send(client, channel, message)
        processed += 1
        if ok:
            await store.mark_sent(row["id"], now)
        else:
            attempts = row["attempts"] + 1
            next_at = None if attempts >= settings.max_attempts else now + backoff_delay(attempts)
            await store.mark_failed(row["id"], attempts, detail, next_at)
    return processed
