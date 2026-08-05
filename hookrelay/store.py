"""SQLite persistence: events, decisions, deliveries, silences.

One decision row per event — the router's memory of WHY. Deliveries are an
outbox: a row is a promise to send, and every promise ends in exactly one of
sent / dead, with the attempt count and last error kept in the open.
"""

from __future__ import annotations

import json
import time
from typing import Any

import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    received_at REAL NOT NULL,
    fingerprint TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    level TEXT NOT NULL DEFAULT 'info',
    fields_json TEXT NOT NULL DEFAULT '{}',
    payload_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ix_events_fp_time ON events (fingerprint, received_at);

CREATE TABLE IF NOT EXISTS decisions (
    event_id INTEGER PRIMARY KEY REFERENCES events (id),
    outcome TEXT NOT NULL,               -- routed | skipped
    skip_code TEXT,                      -- duplicate | silenced | no_route
    channels_json TEXT NOT NULL DEFAULT '[]',
    steps_json TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES events (id),
    channel TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',   -- queued | sent | dead
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at REAL NOT NULL,
    sent_at REAL,
    last_error TEXT
);
CREATE INDEX IF NOT EXISTS ix_deliveries_due ON deliveries (status, next_attempt_at);
CREATE INDEX IF NOT EXISTS ix_deliveries_channel_sent ON deliveries (channel, sent_at);

CREATE TABLE IF NOT EXISTS silences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,                -- '*' silences everything
    until_ts REAL NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
"""


class Store:
    def __init__(self, path: str) -> None:
        self._path = path
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        self._db = await aiosqlite.connect(self._path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(_SCHEMA)
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        assert self._db is not None, "Store.open() was not called"
        return self._db

    # ── events & decisions ────────────────────────────────────────────────

    async def insert_event(self, source: str, fp: str, extracted: dict[str, Any], payload_json: str, now: float) -> int:
        cursor = await self.db.execute(
            "INSERT INTO events (source, received_at, fingerprint, title, body, level, fields_json, payload_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                source,
                now,
                fp,
                extracted["title"],
                extracted["body"],
                extracted["level"],
                json.dumps(extracted["fields"], ensure_ascii=False),
                payload_json,
            ),
        )
        await self.db.commit()
        return int(cursor.lastrowid or 0)

    async def recent_duplicate(self, fp: str, window_seconds: int, now: float) -> dict[str, Any] | None:
        cursor = await self.db.execute(
            "SELECT id, received_at FROM events WHERE fingerprint = ? AND received_at >= ? ORDER BY id DESC LIMIT 1",
            (fp, now - window_seconds),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def insert_decision(
        self, event_id: int, outcome: str, skip_code: str | None, channels: list[str], steps: list[dict[str, Any]]
    ) -> None:
        await self.db.execute(
            "INSERT INTO decisions (event_id, outcome, skip_code, channels_json, steps_json) VALUES (?, ?, ?, ?, ?)",
            (event_id, outcome, skip_code, json.dumps(channels), json.dumps(steps, ensure_ascii=False)),
        )
        await self.db.commit()

    # ── deliveries ────────────────────────────────────────────────────────

    async def enqueue_delivery(self, event_id: int, channel: str, now: float) -> None:
        await self.db.execute(
            "INSERT INTO deliveries (event_id, channel, next_attempt_at) VALUES (?, ?, ?)",
            (event_id, channel, now),
        )
        await self.db.commit()

    async def due_deliveries(self, now: float, limit: int = 50) -> list[dict[str, Any]]:
        cursor = await self.db.execute(
            "SELECT d.*, e.source, e.title, e.body, e.level, e.fields_json, e.received_at"
            " FROM deliveries d JOIN events e ON e.id = d.event_id"
            " WHERE d.status = 'queued' AND d.next_attempt_at <= ? ORDER BY d.id LIMIT ?",
            (now, limit),
        )
        return [dict(row) for row in await cursor.fetchall()]

    async def sent_count_since(self, channel: str, since: float) -> int:
        cursor = await self.db.execute(
            "SELECT count(*) AS n FROM deliveries WHERE channel = ? AND sent_at >= ?", (channel, since)
        )
        row = await cursor.fetchone()
        return int(row["n"]) if row else 0

    async def defer_delivery(self, delivery_id: int, until: float) -> None:
        """Rate-limit pushback: not an attempt, not an error — just later."""
        await self.db.execute("UPDATE deliveries SET next_attempt_at = ? WHERE id = ?", (until, delivery_id))
        await self.db.commit()

    async def mark_sent(self, delivery_id: int, now: float) -> None:
        await self.db.execute(
            "UPDATE deliveries SET status = 'sent', sent_at = ?, last_error = NULL WHERE id = ?", (now, delivery_id)
        )
        await self.db.commit()

    async def mark_failed(self, delivery_id: int, attempts: int, error: str, next_at: float | None) -> None:
        """next_at None = out of attempts → dead letter, visible forever."""
        if next_at is None:
            await self.db.execute(
                "UPDATE deliveries SET status = 'dead', attempts = ?, last_error = ? WHERE id = ?",
                (attempts, error[:500], delivery_id),
            )
        else:
            await self.db.execute(
                "UPDATE deliveries SET attempts = ?, last_error = ?, next_attempt_at = ? WHERE id = ?",
                (attempts, error[:500], next_at, delivery_id),
            )
        await self.db.commit()

    # ── silences ──────────────────────────────────────────────────────────

    async def active_silence(self, source: str, now: float) -> dict[str, Any] | None:
        cursor = await self.db.execute(
            "SELECT * FROM silences WHERE (source = ? OR source = '*') AND until_ts > ? ORDER BY id DESC LIMIT 1",
            (source, now),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def add_silence(self, source: str, until_ts: float, note: str, now: float) -> int:
        cursor = await self.db.execute(
            "INSERT INTO silences (source, until_ts, note, created_at) VALUES (?, ?, ?, ?)",
            (source, until_ts, note, now),
        )
        await self.db.commit()
        return int(cursor.lastrowid or 0)

    async def delete_silence(self, silence_id: int) -> bool:
        cursor = await self.db.execute("DELETE FROM silences WHERE id = ?", (silence_id,))
        await self.db.commit()
        return cursor.rowcount > 0

    async def list_silences(self, now: float) -> list[dict[str, Any]]:
        cursor = await self.db.execute("SELECT * FROM silences WHERE until_ts > ? ORDER BY id DESC", (now,))
        return [dict(row) for row in await cursor.fetchall()]

    # ── status view ───────────────────────────────────────────────────────

    async def queue_counts(self) -> dict[str, int]:
        cursor = await self.db.execute("SELECT status, count(*) AS n FROM deliveries GROUP BY status")
        counts = {row["status"]: int(row["n"]) for row in await cursor.fetchall()}
        return {"queued": counts.get("queued", 0), "sent": counts.get("sent", 0), "dead": counts.get("dead", 0)}

    async def recent_events(self, limit: int = 50) -> list[dict[str, Any]]:
        cursor = await self.db.execute(
            "SELECT e.id, e.source, e.received_at, e.title, e.level,"
            "       d.outcome, d.skip_code, d.channels_json, d.steps_json"
            " FROM events e LEFT JOIN decisions d ON d.event_id = e.id"
            " ORDER BY e.id DESC LIMIT ?",
            (limit,),
        )
        events = [dict(row) for row in await cursor.fetchall()]
        for event in events:
            # Parsed, not raw: the status page (and any client) gets the
            # decision trace as data, because WHY is the product.
            event["channels"] = json.loads(event.pop("channels_json") or "[]")
            event["steps"] = json.loads(event.pop("steps_json") or "[]")
            cursor = await self.db.execute(
                "SELECT channel, status, attempts, last_error FROM deliveries WHERE event_id = ? ORDER BY id",
                (event["id"],),
            )
            event["deliveries"] = [dict(row) for row in await cursor.fetchall()]
        return events


def now_ts() -> float:
    return time.time()
