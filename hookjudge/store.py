"""The ledger: one row per judged event.

Same promise as the pipe's, narrowed to this service's one job — every event
that arrived has a verdict on record, with WHICH route produced it, what it
cost, and whether the result made it back. Reuse reads from this table too, so
the memory and the account are the same thing rather than a cache beside a log.
"""

from __future__ import annotations

import json
import time
from typing import Any

import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS judgements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    received_at REAL NOT NULL,
    source TEXT NOT NULL,
    identity TEXT NOT NULL,
    correlation_id TEXT,
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    -- The identity fields, persisted because the RETURN leg rebuilds the event
    -- from this row: without them the pipe receives an empty identity and the
    -- breadcrumb it lays out is blank (it was).
    fields_json TEXT NOT NULL DEFAULT '{}',
    is_recovery INTEGER NOT NULL DEFAULT 0,
    summary TEXT NOT NULL DEFAULT '',
    importance TEXT NOT NULL DEFAULT '',
    event_type TEXT NOT NULL DEFAULT '',
    impact_scope TEXT NOT NULL DEFAULT '',
    route TEXT NOT NULL DEFAULT '',
    degraded_reason TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    cost REAL NOT NULL DEFAULT 0,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    -- The return leg: queued -> sent | dead. A judgement nobody received is
    -- not a delivered judgement, and the ledger must not pretend otherwise.
    return_status TEXT NOT NULL DEFAULT 'queued',
    return_attempts INTEGER NOT NULL DEFAULT 0,
    return_error TEXT
);
CREATE INDEX IF NOT EXISTS ix_identity_time ON judgements (identity, received_at);
CREATE INDEX IF NOT EXISTS ix_return ON judgements (return_status, id);
"""


class Store:
    def __init__(self, path: str) -> None:
        self._path = path
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        self._db = await aiosqlite.connect(self._path)
        try:
            self._db.row_factory = aiosqlite.Row
            await self._db.executescript(_SCHEMA)
            await self._db.commit()
        except Exception:
            # A connection runs a thread; leaving it open on a failed start
            # turns a clear schema error into a process that will not exit.
            await self._db.close()
            self._db = None
            raise

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        assert self._db is not None, "Store.open() was not called"
        return self._db

    async def prior_verdict(self, identity: str, window_seconds: int, now: float) -> dict[str, Any] | None:
        """The last real judgement for this identity inside the window.

        Only AI verdicts are reusable: reusing a rule verdict would spread a
        degraded answer across a whole storm, and reusing a reuse would let one
        judgement live forever by being re-served.
        """
        cursor = await self.db.execute(
            "SELECT summary, importance, event_type, impact_scope, model FROM judgements"
            " WHERE identity = ? AND route = 'ai' AND received_at >= ? ORDER BY id DESC LIMIT 1",
            (identity, now - max(1, window_seconds)),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def record(self, event: Any, verdict: Any, latency_ms: int) -> int:
        cursor = await self.db.execute(
            "INSERT INTO judgements (received_at, source, identity, correlation_id, title, body, fields_json,"
            " is_recovery, summary, importance, event_type, impact_scope, route, degraded_reason, model, tokens_in,"
            " tokens_out, cost, latency_ms) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                event.received_at,
                event.source,
                event.identity,
                event.correlation_id or None,
                event.title,
                event.body[:4000],
                json.dumps(event.fields, ensure_ascii=False),
                1 if event.is_recovery else 0,
                verdict.summary,
                verdict.importance,
                verdict.event_type,
                verdict.impact_scope,
                verdict.route,
                verdict.degraded_reason,
                verdict.model,
                verdict.tokens_in,
                verdict.tokens_out,
                verdict.cost,
                latency_ms,
            ),
        )
        await self.db.commit()
        return int(cursor.lastrowid or 0)

    async def pending_returns(self, limit: int = 50) -> list[dict[str, Any]]:
        cursor = await self.db.execute(
            "SELECT * FROM judgements WHERE return_status = 'queued' ORDER BY id LIMIT ?", (limit,)
        )
        return [dict(row) for row in await cursor.fetchall()]

    async def mark_return(self, row_id: int, status: str, attempts: int, error: str | None) -> None:
        await self.db.execute(
            "UPDATE judgements SET return_status = ?, return_attempts = ?, return_error = ? WHERE id = ?",
            (status, attempts, (error or "")[:400] or None, row_id),
        )
        await self.db.commit()

    async def recent(self, limit: int = 50, *, route: str | None = None, q: str | None = None) -> list[dict[str, Any]]:
        clauses, params = [], []
        if route:
            clauses.append("route = ?")
            params.append(route)
        if q:
            clauses.append("(title LIKE ? OR summary LIKE ?)")
            params += [f"%{q}%", f"%{q}%"]
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(max(1, min(500, limit)))
        cursor = await self.db.execute(f"SELECT * FROM judgements{where} ORDER BY id DESC LIMIT ?", tuple(params))
        return [dict(row) for row in await cursor.fetchall()]

    async def summary(self, since: float) -> dict[str, Any]:
        cursor = await self.db.execute(
            "SELECT route, count(*) AS n, coalesce(sum(cost),0) AS cost, coalesce(avg(latency_ms),0) AS latency"
            " FROM judgements WHERE received_at >= ? GROUP BY route",
            (since,),
        )
        routes = {
            str(row["route"]): {
                "count": int(row["n"]),
                "cost": round(float(row["cost"]), 6),
                "avg_latency_ms": int(row["latency"]),
            }
            for row in await cursor.fetchall()
        }
        cursor = await self.db.execute(
            "SELECT return_status, count(*) AS n FROM judgements WHERE received_at >= ? GROUP BY return_status",
            (since,),
        )
        returns = {str(row["return_status"]): int(row["n"]) for row in await cursor.fetchall()}
        judged = sum(r["count"] for r in routes.values())
        paid = routes.get("ai", {}).get("count", 0)
        return {
            "judged": judged,
            "routes": routes,
            "returns": returns,
            "cost": round(sum(r["cost"] for r in routes.values()), 6),
            # The number the cost conversation actually turns on: how many of
            # the judgements we served did we have to pay a model for?
            "paid_ratio_pct": round(100.0 * paid / judged, 1) if judged else 0.0,
        }

    async def purge_older_than(self, cutoff: float) -> int:
        cursor = await self.db.execute(
            "DELETE FROM judgements WHERE received_at < ? AND return_status != 'queued'", (cutoff,)
        )
        await self.db.commit()
        return int(cursor.rowcount or 0)


def now_ts() -> float:
    return time.time()


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)
