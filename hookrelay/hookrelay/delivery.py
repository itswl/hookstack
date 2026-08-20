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
import logging
from typing import Any

import httpx

from hookrelay import actions, channels, metrics
from hookrelay.alarm import SelfAlarm
from hookrelay.breaker import CircuitBreaker
from hookrelay.config import Config
from hookrelay.settings import Settings
from hookrelay.store import Store

logger = logging.getLogger("hookrelay.delivery")

_RATE_DEFER_SECONDS = 10
_BREAKER_DEFER_SECONDS = 15
_BACKOFF_BASE_SECONDS = 30
_BACKOFF_CAP_SECONDS = 600


def _mint_card_actions(message: dict[str, Any], cfg: Config, settings: Settings, now: float) -> None:
    """Replace a brain's action DECLARATIONS with signed buttons, in place.

    Silently leaves the payload alone when there is nothing to do — no secret,
    no configured kinds, or a payload that is not a brain's result. A verdict
    must reach its channel whether or not this deployment offers buttons.
    """
    if not settings.action_secret or not cfg.card_actions:
        return
    payload = message.get("payload")
    if not isinstance(payload, dict):
        return
    declared = payload.get("actions")
    if not isinstance(declared, list) or not declared:
        return
    raw_meta = payload.get("meta")
    meta: dict[str, Any] = raw_meta if isinstance(raw_meta, dict) else {}
    payload["actions"] = actions.offered(
        settings.action_secret,
        [item for item in declared if isinstance(item, dict)],
        {kind: {"params": spec.params} for kind, spec in cfg.card_actions.items()},
        event_id=int(message["event_id"]),
        # The brain's correlation_id points at the ORIGINAL alert; this row is
        # the verdict's own event. A button means "act on the alert", so the
        # correlation is the handle that matters and the event id is the trail.
        correlation_id=str(meta.get("correlation_id") or message.get("_correlation_id") or ""),
        now=now,
        ttl_seconds=settings.action_ttl_seconds,
    )


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

    # return_exceptions is not politeness here — it is what stops one channel
    # group's failure from ABANDONING the others. A bare gather re-raises the
    # first exception the moment it happens, while the sibling tasks keep
    # running unawaited: process_due is already gone, the next tick re-picks
    # rows those orphans are half-way through (due_deliveries selects on
    # status/next_attempt_at, it claims nothing), and a downstream that has
    # already received the alert receives it a second time. Collecting the
    # failures instead means every group is finished before we return, so a
    # broken channel costs its own rows a tick rather than somebody else's
    # duplicate.
    counts = await asyncio.gather(
        *(_drain_channel(store, cfg, settings, client, now, group, alarm, breaker) for group in by_channel.values()),
        return_exceptions=True,
    )
    processed = 0
    for channel_name, count in zip(by_channel, counts, strict=True):
        if isinstance(count, BaseException):
            # Loud, named, and not re-raised: those rows are still queued and
            # still due, so the next tick retries them — but the sends that DID
            # succeed elsewhere in this batch are finished, and throwing now
            # would only hide that they were.
            logger.error("delivery batch for channel %s failed — its rows stay due", channel_name, exc_info=count)
            continue
        processed += count
    return processed


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
                # The breaker may have just handed this row the half-open probe
                # slot. We are not going to use it, so give it back — holding it
                # while sending nothing stalls the channel until a restart.
                if breaker is not None:
                    breaker.release_probe(channel.name)
                continue

        message = {
            "event_id": row["event_id"],
            "source": row["source"],
            "title": row["title"],
            "body": row["body"],
            "level": row["level"],
            "fields": json.loads(row["fields_json"] or "{}"),
            "received_at": row["received_at"],
            # Only when the source template stated it (tri-state column);
            # absent means the receiver falls back to its own detection.
            **({"is_recovery": bool(row["is_recovery"])} if row["is_recovery"] is not None else {}),
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
        # A brain DECLARED which actions its verdict deserves; the pipe decides
        # which are on offer here and signs them. Done at delivery rather than
        # in the renderer so the token's clock starts when the card is actually
        # sent, and so the renderer stays a pure function of the payload.
        _mint_card_actions(message, cfg, settings, now)
        # Where an action LINK points, for the dialects that cannot call back.
        # Carried on the message like the other two underscore keys, because a
        # builder gets a message and never the settings.
        if settings.public_url:
            message["_action_base"] = settings.public_url
        ok, detail, body = await channels.send(client, channel, message)
        sent_body = channels.redact_for_ledger(body)
        processed += 1
        if ok:
            await store.mark_sent(row["id"], now, sent_body)
            metrics.record_delivery(channel.name, "sent")
            if breaker is not None:
                breaker.record_success(channel.name)
        else:
            attempts = row["attempts"] + 1
            next_at = None if attempts >= settings.max_attempts else now + backoff_delay(attempts)
            await store.mark_failed(row["id"], attempts, detail, next_at, sent_body)
            metrics.record_delivery(channel.name, "dead" if next_at is None else "failed")
            if breaker is not None:
                breaker.record_failure(channel.name, now)
            if next_at is None and alarm is not None:
                await alarm.dead_letter(client, channel=channel.name, event_id=row["event_id"], error=detail, now=now)
    return processed
