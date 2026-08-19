"""SQLite persistence: events, decisions, deliveries, silences.

One decision row per event — the router's memory of WHY. Deliveries are an
outbox: a row is a promise to send, and every promise ends in exactly one of
sent / dead, with the attempt count and last error kept in the open.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
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
    payload_json TEXT NOT NULL DEFAULT '{}',
    -- The id this event QUOTED back, when a brain echoed ours. First-class and
    -- indexed rather than buried in the trace, because gathering N brains'
    -- work under one original alert is a query, not a scan.
    correlation_id TEXT,
    -- Tri-state recovery flag: NULL = the source template stated nothing
    -- (receivers fall back to their own detection), 0/1 = the upstream
    -- platform stated the fact. Deliberately NOT a field: fields build
    -- identity, and a flag that flips between firing and recovery would
    -- split the pair.
    is_recovery INTEGER
);
CREATE INDEX IF NOT EXISTS ix_events_fp_time ON events (fingerprint, received_at);
-- ix_events_correlation is created in _migrate(), NOT here: this script runs
-- against ledgers written by older builds, and indexing a column that table
-- does not have yet fails the whole open. (It did — on a ledger with 173 real
-- events in it, which is why the migration test exists.)

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
    last_error TEXT,
    -- The exact body of the last attempt, as it left the socket. Body only,
    -- never the headers: headers carry signatures and tokens.
    sent_body TEXT
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
        # Set by the app to a Live.changed; None everywhere else, so the store
        # works unchanged with nobody watching.
        self.on_change: Callable[[], None] | None = None
        self._path = path
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        self._db = await aiosqlite.connect(self._path)
        try:
            self._db.row_factory = aiosqlite.Row
            await self._db.executescript(_SCHEMA)
            await self._migrate()
            await self._db.commit()
        except Exception:
            # The connection runs a thread; leaving it open on a failed start
            # hangs the process at exit and turns a clear schema error into a
            # mysterious timeout (it turned one into a killed CI job).
            await self._db.close()
            self._db = None
            raise

    async def _migrate(self) -> None:
        """Additive column migrations for ledgers created by older builds.

        CREATE TABLE IF NOT EXISTS never adds a column to a table that already
        exists, so a running relay would keep its old shape and every query
        touching the new column would fail — the production ledger has 173
        events in it and must survive the upgrade.
        """
        db = self._db
        if db is None:  # open() migrates right after connecting; anything else is a bug
            raise RuntimeError("store is not open")
        cursor = await db.execute("PRAGMA table_info(events)")
        columns = {str(row["name"]) for row in await cursor.fetchall()}
        if "correlation_id" not in columns:
            await db.execute("ALTER TABLE events ADD COLUMN correlation_id TEXT")
        if "is_recovery" not in columns:
            await db.execute("ALTER TABLE events ADD COLUMN is_recovery INTEGER")
        # Always, and only after the column is certain to exist.
        await db.execute("CREATE INDEX IF NOT EXISTS ix_events_correlation ON events (correlation_id)")
        cursor = await db.execute("PRAGMA table_info(deliveries)")
        delivery_columns = {str(row["name"]) for row in await cursor.fetchall()}
        if "sent_body" not in delivery_columns:
            await db.execute("ALTER TABLE deliveries ADD COLUMN sent_body TEXT")

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Store.open() was not called")
        return self._db

    # ── events & decisions ────────────────────────────────────────────────

    async def insert_event(
        self,
        source: str,
        fp: str,
        extracted: dict[str, Any],
        payload_json: str,
        now: float,
        correlation_id: str | None = None,
    ) -> int:
        cursor = await self.db.execute(
            "INSERT INTO events (source, received_at, fingerprint, title, body, level, fields_json, payload_json,"
            "                   correlation_id, is_recovery)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                source,
                now,
                fp,
                extracted["title"],
                extracted["body"],
                extracted["level"],
                json.dumps(extracted["fields"], ensure_ascii=False),
                payload_json,
                correlation_id or None,
                # Tri-state: absent key -> NULL (nothing stated), bool -> 0/1.
                (int(bool(extracted["is_recovery"])) if "is_recovery" in extracted else None),
            ),
        )
        await self.db.commit()
        self._announce()
        return int(cursor.lastrowid or 0)

    def _announce(self) -> None:
        """Say that the ledger moved; the boards decide what to refetch."""
        if self.on_change is not None:
            self.on_change()

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
            (
                event_id,
                outcome,
                skip_code,
                json.dumps(channels, ensure_ascii=False),
                json.dumps(steps, ensure_ascii=False),
            ),
        )
        await self.db.commit()
        self._announce()

    # ── deliveries ────────────────────────────────────────────────────────

    async def enqueue_delivery(self, event_id: int, channel: str, now: float) -> None:
        await self.db.execute(
            "INSERT INTO deliveries (event_id, channel, next_attempt_at) VALUES (?, ?, ?)",
            (event_id, channel, now),
        )
        await self.db.commit()
        self._announce()

    async def due_deliveries(self, now: float, limit: int = 50) -> list[dict[str, Any]]:
        cursor = await self.db.execute(
            "SELECT d.*, e.source, e.title, e.body, e.level, e.fields_json, e.payload_json, e.received_at,"
            " e.is_recovery"
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
        self._announce()

    async def mark_sent(self, delivery_id: int, now: float, sent_body: str | None = None) -> None:
        await self.db.execute(
            "UPDATE deliveries SET status = 'sent', sent_at = ?, last_error = NULL, sent_body = ? WHERE id = ?",
            (now, sent_body, delivery_id),
        )
        await self.db.commit()
        self._announce()

    async def mark_failed(
        self, delivery_id: int, attempts: int, error: str, next_at: float | None, sent_body: str | None = None
    ) -> None:
        """next_at None = out of attempts → dead letter, visible forever.

        A None sent_body means "this attempt produced no bytes" — the builder
        refused, or the channel vanished from config — and that must not erase
        what an earlier attempt actually posted. COALESCE keeps the last real
        bytes: overwriting them with NULL destroyed the only record of what the
        receiver was sent, on the exact rows an operator opens to find out.
        """
        if next_at is None:
            await self.db.execute(
                "UPDATE deliveries SET status = 'dead', attempts = ?, last_error = ?, "
                "sent_body = COALESCE(?, sent_body) WHERE id = ?",
                (attempts, error[:500], sent_body, delivery_id),
            )
        else:
            await self.db.execute(
                "UPDATE deliveries SET attempts = ?, last_error = ?, next_attempt_at = ?, "
                "sent_body = COALESCE(?, sent_body) WHERE id = ?",
                (attempts, error[:500], next_at, sent_body, delivery_id),
            )
        await self.db.commit()
        self._announce()

    async def retry_delivery(self, delivery_id: int, now: float) -> bool:
        """Operator second chance: a dead delivery back to queued, due now.
        Attempts reset — the operator's judgement outranks the backoff ledger."""
        cursor = await self.db.execute(
            "UPDATE deliveries SET status = 'queued', attempts = 0, next_attempt_at = ? "
            "WHERE id = ? AND status = 'dead'",
            (now, delivery_id),
        )
        await self.db.commit()
        return cursor.rowcount > 0

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
        self._announce()
        return int(cursor.lastrowid or 0)

    async def delete_silence(self, silence_id: int) -> bool:
        cursor = await self.db.execute("DELETE FROM silences WHERE id = ?", (silence_id,))
        await self.db.commit()
        return cursor.rowcount > 0

    async def list_silences(self, now: float) -> list[dict[str, Any]]:
        cursor = await self.db.execute("SELECT * FROM silences WHERE until_ts > ? ORDER BY id DESC", (now,))
        return [dict(row) for row in await cursor.fetchall()]

    async def round_trip(self, event_id: int) -> dict[str, Any] | None:
        """Assemble one alert's whole journey: the original, where it fanned
        out to, and what each brain sent back.

        This is the view that makes several processing systems COMPARABLE — the
        same payload went to each of them, so the differences in what came back
        are differences in their judgement, not in their input.
        """
        origin = await self._event_row(event_id)
        if origin is None:
            return None
        # Returns quote the id we stamped on egress. If this event is itself a
        # return, gather around its origin instead so the view is the same from
        # either end.
        anchor_id = event_id
        quoted = str(origin.get("correlation_id") or "")
        if quoted.startswith("hr-") and quoted[3:].isdigit():
            anchor = await self._event_row(int(quoted[3:]))
            if anchor is not None:
                origin, anchor_id = anchor, int(quoted[3:])

        cursor = await self.db.execute(
            "SELECT id FROM events WHERE correlation_id = ? ORDER BY id",
            (f"hr-{anchor_id}",),
        )
        returns = [await self._event_row(int(row["id"])) for row in await cursor.fetchall()]
        for item in returns:
            if item is not None:
                item["latency_seconds"] = round(float(item["received_at"]) - float(origin["received_at"]), 3)
        return {"origin": origin, "returns": [r for r in returns if r is not None]}

    async def _event_row(self, event_id: int) -> dict[str, Any] | None:
        cursor = await self.db.execute(
            "SELECT e.id, e.source, e.received_at, e.title, e.body, e.level, e.fields_json, e.correlation_id,"
            "       e.payload_json, d.outcome, d.skip_code, d.channels_json, d.steps_json"
            " FROM events e LEFT JOIN decisions d ON d.event_id = e.id WHERE e.id = ?",
            (event_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        event = dict(row)
        event["fields"] = json.loads(event.pop("fields_json") or "{}")
        event["channels"] = json.loads(event.pop("channels_json") or "[]")
        event["steps"] = json.loads(event.pop("steps_json") or "[]")
        # Both halves of the forensic record: the payload exactly as received…
        event["payload"] = json.loads(event.pop("payload_json") or "null")
        cursor = await self.db.execute(
            # …and per delivery, the exact body that left the socket.
            "SELECT id, channel, status, attempts, last_error, sent_at, sent_body"
            " FROM deliveries WHERE event_id = ? ORDER BY id",
            (event_id,),
        )
        event["deliveries"] = [dict(r) for r in await cursor.fetchall()]
        return event

    # ── retention ─────────────────────────────────────────────────────────

    async def purge_older_than(self, cutoff: float, now: float) -> dict[str, int]:
        """Retention: this ledger must not grow forever once ALL traffic rides
        through it. An event is purgeable when it is older than the cutoff AND
        none of its deliveries is still queued — a promise in flight is never
        deleted out from under the worker. Expired silences go with them."""
        cursor = await self.db.execute(
            "SELECT id FROM events e WHERE e.received_at < ?"
            " AND NOT EXISTS (SELECT 1 FROM deliveries d WHERE d.event_id = e.id AND d.status = 'queued')",
            (cutoff,),
        )
        ids = [int(row["id"]) for row in await cursor.fetchall()]
        for start in range(0, len(ids), 500):
            chunk = ids[start : start + 500]
            marks = ",".join("?" for _ in chunk)
            # `marks` is only "?" placeholders; the values are parametrized.
            await self.db.execute(f"DELETE FROM deliveries WHERE event_id IN ({marks})", chunk)  # nosec B608
            await self.db.execute(f"DELETE FROM decisions WHERE event_id IN ({marks})", chunk)  # nosec B608
            await self.db.execute(f"DELETE FROM events WHERE id IN ({marks})", chunk)  # nosec B608
        cursor = await self.db.execute("DELETE FROM silences WHERE until_ts < ?", (now,))
        silences = cursor.rowcount
        await self.db.commit()
        return {"events": len(ids), "silences": max(0, silences)}

    # ── status view ───────────────────────────────────────────────────────

    async def queue_counts(self) -> dict[str, int]:
        cursor = await self.db.execute("SELECT status, count(*) AS n FROM deliveries GROUP BY status")
        counts = {row["status"]: int(row["n"]) for row in await cursor.fetchall()}
        return {"queued": counts.get("queued", 0), "sent": counts.get("sent", 0), "dead": counts.get("dead", 0)}

    async def recent_events(
        self,
        limit: int = 50,
        *,
        source: str | None = None,
        outcome: str | None = None,
        skip_code: str | None = None,
        query: str | None = None,
        before_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """Latest events, newest first, with optional filters.

        Once a whole estate rides through one relay, "did alert X arrive?" is
        the question the ledger exists to answer — a fixed window of 50 cannot.
        Filters compose (AND) and paginate with before_id.
        """
        clauses: list[str] = []
        params: list[Any] = []
        if source:
            clauses.append("e.source = ?")
            params.append(source)
        if outcome:
            clauses.append("d.outcome = ?")
            params.append(outcome)
        if skip_code:
            clauses.append("d.skip_code = ?")
            params.append(skip_code)
        if query:
            clauses.append("(e.title LIKE ? OR e.body LIKE ?)")
            params += [f"%{query}%", f"%{query}%"]
        if before_id:
            clauses.append("e.id < ?")
            params.append(int(before_id))
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(max(1, min(500, int(limit))))
        # `where` is built from constant clause fragments; values are parametrized.
        cursor = await self.db.execute(
            "SELECT e.id, e.source, e.received_at, e.title, e.level,"  # nosec B608
            "       d.outcome, d.skip_code, d.channels_json, d.steps_json"
            " FROM events e LEFT JOIN decisions d ON d.event_id = e.id"
            f"{where}"
            " ORDER BY e.id DESC LIMIT ?",
            tuple(params),
        )
        events = [dict(row) for row in await cursor.fetchall()]
        for event in events:
            # Parsed, not raw: the status page (and any client) gets the
            # decision trace as data, because WHY is the product.
            event["channels"] = json.loads(event.pop("channels_json") or "[]")
            event["steps"] = json.loads(event.pop("steps_json") or "[]")
            cursor = await self.db.execute(
                "SELECT id, channel, status, attempts, last_error FROM deliveries WHERE event_id = ? ORDER BY id",
                (event["id"],),
            )
            event["deliveries"] = [dict(row) for row in await cursor.fetchall()]
        return events


def now_ts() -> float:
    return time.time()
