"""SQLite persistence: events, decisions, deliveries, silences.

One decision row per event — the router's memory of WHY. Deliveries are an
outbox: a row is a promise to send, and every promise ends in exactly one of
sent / dead, with the attempt count and last error kept in the open.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

import aiosqlite

logger = logging.getLogger("hookrelay.store")

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
    is_recovery INTEGER,
    -- When a second delivery was sent because nobody had touched this alert.
    -- NULL = never escalated, which is also what every row says when the
    -- feature is off. Stamped BEFORE the deliveries are enqueued, so it bounds
    -- the escalation to one per event rather than one per worker tick.
    escalated_at REAL
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
-- Every read of this table that is not the worker's queue looks it up BY EVENT:
-- the status page's ledger rows, /trace, and the retention sweep's "is anything
-- still queued for this event". Without an index each of those is a full scan,
-- and /status asks it once per event on the page.
CREATE INDEX IF NOT EXISTS ix_deliveries_event ON deliveries (event_id);

CREATE TABLE IF NOT EXISTS silences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,                -- '*' silences everything
    until_ts REAL NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);

-- Every card button a human actually pressed. Two jobs in one table:
--   single use — `jti` is UNIQUE, so the second press of a button that spends
--     money (a follow-up turn, an approved command) is refused by identity
--     rather than by guessing. A card forwarded into a group chat is a token in
--     everyone's scrollback; this is what stops it being a reusable key.
--   the human half of the timeline — the ledger recorded what the machine did
--     to an alert and stopped there. /trace can now answer "and what did a
--     person do about it", which is the question a morning review opens with.
CREATE TABLE IF NOT EXISTS card_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    jti TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    event_id INTEGER,
    correlation_id TEXT NOT NULL DEFAULT '',
    actor TEXT NOT NULL DEFAULT '',       -- opaque IM user id; never a name
    outcome TEXT NOT NULL DEFAULT '',     -- what the pipe did with it
    pressed_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_card_actions_event ON card_actions (event_id);
CREATE INDEX IF NOT EXISTS ix_card_actions_correlation ON card_actions (correlation_id);
"""


class Transaction:
    """The write side of one atomic unit — statements only, never a commit.

    Every method here is a plain statement against the shared connection. What
    makes them a unit is Store.transaction(), which holds the write lock around
    them and commits once at the end; a commit in here would defeat the whole
    point by ending the transaction early. That is also why this class is
    handed out rather than exposed: you cannot get one without the lock.

    Reads live here too, so a read taken as part of a decision sees the same
    snapshot as the writes that follow it — which is what closes the dedup race:
    "is this a duplicate" and "insert it" used to be two transactions with a gap
    wide enough for a second copy of the same webhook to pass through.
    """

    def __init__(self, db: aiosqlite.Connection) -> None:
        self._db = db

    async def recent_duplicate(self, fp: str, window_seconds: int, now: float) -> dict[str, Any] | None:
        cursor = await self._db.execute(
            "SELECT id, received_at FROM events WHERE fingerprint = ? AND received_at >= ? ORDER BY id DESC LIMIT 1",
            (fp, now - window_seconds),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def insert_event(
        self,
        source: str,
        fp: str,
        extracted: dict[str, Any],
        payload_json: str,
        now: float,
        correlation_id: str | None = None,
    ) -> int:
        cursor = await self._db.execute(
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
        return int(cursor.lastrowid or 0)

    async def insert_decision(
        self, event_id: int, outcome: str, skip_code: str | None, channels: list[str], steps: list[dict[str, Any]]
    ) -> None:
        await self._db.execute(
            "INSERT INTO decisions (event_id, outcome, skip_code, channels_json, steps_json) VALUES (?, ?, ?, ?, ?)",
            (
                event_id,
                outcome,
                skip_code,
                json.dumps(channels, ensure_ascii=False),
                json.dumps(steps, ensure_ascii=False),
            ),
        )

    async def enqueue_deliveries(self, event_id: int, channels: list[str], now: float) -> None:
        """Every channel this event routed to, in one statement.

        executemany rather than a loop of inserts: the fan-out is the common case
        and each insert was its own commit and its own board announcement, so a
        four-channel event cost four fsyncs and woke every open board four times.
        """
        if not channels:
            return
        await self._db.executemany(
            "INSERT INTO deliveries (event_id, channel, next_attempt_at) VALUES (?, ?, ?)",
            [(event_id, channel, now) for channel in channels],
        )


class Store:
    def __init__(self, path: str) -> None:
        # Set by the app to a Live.changed; None everywhere else, so the store
        # works unchanged with nobody watching.
        self.on_change: Callable[[], None] | None = None
        self._path = path
        self._db: aiosqlite.Connection | None = None
        # EVERY writer holds this — see transaction() for why a single-statement
        # write needs it just as much as a grouped one.
        self._write_lock = asyncio.Lock()

    async def open(self) -> None:
        self._db = await aiosqlite.connect(self._path)
        try:
            self._db.row_factory = aiosqlite.Row
            await self._apply_pragmas()
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

    async def _apply_pragmas(self) -> None:
        """Three settings that decide how this ledger behaves under real load.

        open() ran the schema and nothing else, which means the ledger inherited
        SQLite's defaults — tuned for one program touching a file now and then,
        not for a webhook door with a delivery worker writing underneath it and a
        status page reading over the top. The first real outage is where that
        gets found out, so say it here instead:

        journal_mode=WAL — a commit appends to a log instead of writing a
          rollback journal and fsyncing the database file, and a reader no longer
          has to take turns with the writer. That second half is what matters the
          moment somebody opens the ledger with the sqlite3 CLI to answer a
          question during an incident: under the default journal that read blocks
          the delivery worker. WAL is a persistent property of the file, so this
          also upgrades a ledger written by an older build.
        synchronous=NORMAL — one fsync per WAL checkpoint rather than one per
          commit. What survives is a process crash, which is exactly the promise
          the outbox makes; what is traded away is durability across an OS or
          power loss, which is not what this ledger defends against.
        busy_timeout=5000 — wait for a held lock instead of raising SQLITE_BUSY
          on contact. Without it, a lock contended for milliseconds surfaces as
          "database is locked" — an inbound event refused, or a delivery whose
          bookkeeping was lost, for a wait nobody would have noticed.
        """
        db = self._db
        if db is None:  # only ever called from open(), immediately after connect
            raise RuntimeError("store is not open")
        for pragma in ("journal_mode=WAL", "synchronous=NORMAL", "busy_timeout=5000"):
            await db.execute(f"PRAGMA {pragma}")  # nosec B608 — a constant from the tuple above
        cursor = await db.execute("PRAGMA journal_mode")
        row = await cursor.fetchone()
        mode = str(row[0]).lower() if row else "?"
        if mode != "wal":
            # An in-memory or network-mounted file can refuse WAL. Not fatal —
            # the ledger still works — but it must not look like it was applied.
            logger.warning("ledger journal_mode is %s, not wal (a WAL upgrade was requested and refused)", mode)

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
        if "escalated_at" not in columns:
            await db.execute("ALTER TABLE events ADD COLUMN escalated_at REAL")

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Store.open() was not called")
        return self._db

    # ── the atomic unit ───────────────────────────────────────────────────

    @contextlib.asynccontextmanager
    async def transaction(self) -> AsyncIterator[Transaction]:
        """Several writes that land together, or not at all.

        The header above promises an outbox: "a row is a promise to send". An
        event, its decision and its delivery rows were three separate
        transactions, so a crash in the gaps left a `routed` event with fewer
        deliveries than it had channels — an alert the ledger says was sent to
        four places and was sent to two, which is worse than a visible failure
        because nothing looks wrong.

        THE LOCK IS NOT OPTIONAL, and it is why every single-statement writer in
        this file takes it too. All of them share ONE aiosqlite connection, and a
        connection has one transaction: a `commit()` from another task landing
        between two of the statements below would commit half of this unit and
        call it done. Serialising writers is what makes "one transaction" mean
        anything here. The critical section is a handful of local inserts — no
        network, no user code — so nothing waits long on it.
        """
        async with self._write_lock:
            try:
                yield Transaction(self.db)
            except BaseException:
                # Nothing partial survives. Safe to roll back the whole
                # connection precisely BECAUSE the lock means this transaction is
                # the only uncommitted work on it.
                await self.db.rollback()
                raise
            await self.db.commit()
        # One announcement for the unit, not one per row: the boards want to know
        # the ledger moved, not how many statements it took.
        self._announce()

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
        async with self.transaction() as tx:
            return await tx.insert_event(source, fp, extracted, payload_json, now, correlation_id)

    def _announce(self) -> None:
        """Say that the ledger moved; the boards decide what to refetch."""
        if self.on_change is not None:
            self.on_change()

    async def recent_duplicate(self, fp: str, window_seconds: int, now: float) -> dict[str, Any] | None:
        return await Transaction(self.db).recent_duplicate(fp, window_seconds, now)

    async def insert_decision(
        self, event_id: int, outcome: str, skip_code: str | None, channels: list[str], steps: list[dict[str, Any]]
    ) -> None:
        async with self.transaction() as tx:
            await tx.insert_decision(event_id, outcome, skip_code, channels, steps)

    # ── deliveries ────────────────────────────────────────────────────────

    async def enqueue_delivery(self, event_id: int, channel: str, now: float) -> None:
        async with self.transaction() as tx:
            await tx.enqueue_deliveries(event_id, [channel], now)

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
        async with self._write_lock:
            await self.db.execute("UPDATE deliveries SET next_attempt_at = ? WHERE id = ?", (until, delivery_id))
            await self.db.commit()
        self._announce()

    async def mark_sent(self, delivery_id: int, now: float, sent_body: str | None = None) -> None:
        async with self._write_lock:
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
        async with self._write_lock:
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
        async with self._write_lock:
            cursor = await self.db.execute(
                "UPDATE deliveries SET status = 'queued', attempts = 0, next_attempt_at = ? "
                "WHERE id = ? AND status = 'dead'",
                (now, delivery_id),
            )
            await self.db.commit()
            return cursor.rowcount > 0

    async def cold_events(self, *, before: float, levels: tuple[str, ...], limit: int = 50) -> list[dict[str, Any]]:
        """Alerts that were delivered, nobody touched, and have gone cold.

        Three conditions, and each one is load-bearing:

          delivered — an event whose deliveries never left is not unacknowledged,
            it is undelivered, and the dead-letter alarm already owns that story.
          untouched — no row in card_actions for it. That ledger is the only
            evidence this service has that a person was there, which is why this
            question could not be asked before the buttons existed.
          not escalated — `escalated_at` is stamped when the second delivery is
            enqueued, so a cold alert escalates once and not once per tick.

        Returns the ORIGINAL front-door events, never the verdict returns that
        quote them: escalating a return would send the same card twice under two
        names. A return carries correlation_id; a front-door event does not.
        """
        clause = ""
        params: list[Any] = [before]
        if levels:
            clause = f" AND lower(e.level) IN ({','.join('?' for _ in levels)})"
            params.extend(level.lower() for level in levels)
        params.append(max(1, min(limit, 200)))
        cursor = await self.db.execute(
            "SELECT e.id, e.source, e.title, e.level, e.received_at FROM events e"
            " WHERE e.received_at <= ?"
            "   AND e.correlation_id IS NULL"
            "   AND e.escalated_at IS NULL"
            "   AND EXISTS (SELECT 1 FROM deliveries d WHERE d.event_id = e.id AND d.status = 'sent')"
            "   AND NOT EXISTS (SELECT 1 FROM card_actions a WHERE a.event_id = e.id"
            "                     OR a.correlation_id = 'hr-' || e.id)"
            f"{clause}"  # nosec B608 — placeholders only; the fragment is built from the tuple's length
            " ORDER BY e.id LIMIT ?",
            tuple(params),
        )
        return [dict(row) for row in await cursor.fetchall()]

    async def mark_escalated(self, event_id: int, now: float) -> bool:
        """Stamp it before the deliveries are enqueued, so a crash mid-enqueue
        cannot turn one cold alert into an escalation every tick forever."""
        async with self._write_lock:
            cursor = await self.db.execute(
                "UPDATE events SET escalated_at = ? WHERE id = ? AND escalated_at IS NULL", (now, event_id)
            )
            await self.db.commit()
            return cursor.rowcount > 0

    # ── card actions ──────────────────────────────────────────────────────

    async def spend_action(
        self,
        jti: str,
        *,
        kind: str,
        event_id: int | None,
        correlation_id: str,
        actor: str,
        now: float,
    ) -> bool:
        """Claim a token, or False if it was already spent.

        The UNIQUE constraint on jti is the whole mechanism, and claiming BEFORE
        acting is the whole point: two presses arriving together must not both
        get through to something that spends money or restarts a service. The
        row is written first and annotated with its outcome afterwards.
        """
        async with self._write_lock:
            try:
                await self.db.execute(
                    "INSERT INTO card_actions (jti, kind, event_id, correlation_id, actor, pressed_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (jti, kind, event_id, correlation_id, actor[:120], now),
                )
            except aiosqlite.IntegrityError:
                return False
            await self.db.commit()
        self._announce()
        return True

    async def record_action_outcome(self, jti: str, outcome: str) -> None:
        """What the press achieved, for the timeline and for a dispute later."""
        async with self._write_lock:
            await self.db.execute("UPDATE card_actions SET outcome = ? WHERE jti = ?", (outcome[:200], jti))
            await self.db.commit()
        self._announce()

    async def recent_actions(self, limit: int = 50) -> list[dict[str, Any]]:
        cursor = await self.db.execute(
            "SELECT kind, event_id, correlation_id, actor, outcome, pressed_at "
            "FROM card_actions ORDER BY id DESC LIMIT ?",
            (max(1, min(limit, 500)),),
        )
        return [dict(row) for row in await cursor.fetchall()]

    # ── silences ──────────────────────────────────────────────────────────

    async def active_silence(self, source: str, now: float) -> dict[str, Any] | None:
        cursor = await self.db.execute(
            "SELECT * FROM silences WHERE (source = ? OR source = '*') AND until_ts > ? ORDER BY id DESC LIMIT 1",
            (source, now),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def add_silence(self, source: str, until_ts: float, note: str, now: float) -> int:
        async with self._write_lock:
            cursor = await self.db.execute(
                "INSERT INTO silences (source, until_ts, note, created_at) VALUES (?, ?, ?, ?)",
                (source, until_ts, note, now),
            )
            await self.db.commit()
            row_id = int(cursor.lastrowid or 0)
        self._announce()
        return row_id

    async def delete_silence(self, silence_id: int) -> bool:
        async with self._write_lock:
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
        # And what a PERSON did about it. The machine half of this timeline was
        # always here; a morning review opens with the other half.
        cursor = await self.db.execute(
            "SELECT kind, actor, outcome, pressed_at FROM card_actions "
            "WHERE correlation_id = ? OR event_id = ? ORDER BY pressed_at",
            (f"hr-{anchor_id}", anchor_id),
        )
        human = [dict(row) for row in await cursor.fetchall()]
        for act in human:
            act["latency_seconds"] = round(float(act["pressed_at"]) - float(origin["received_at"]), 3)
        return {"origin": origin, "returns": [r for r in returns if r is not None], "human_actions": human}

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
        async with self._write_lock:
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
