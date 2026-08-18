"""The ledger: one row per judged event.

Same promise as the pipe's, narrowed to this service's one job — every event
that arrived has a verdict on record, with WHICH route produced it, what it
cost, and whether the result made it back. Reuse reads from this table too, so
the memory and the account are the same thing rather than a cache beside a log.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS judgements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    received_at REAL NOT NULL,
    source TEXT NOT NULL,
    identity TEXT NOT NULL,
    -- The alert rule behind this firing. Identity keeps instances apart;
    -- this is what a paid verdict can be reused across.
    rule_key TEXT NOT NULL DEFAULT '',
    level TEXT NOT NULL DEFAULT '',
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
        # Set by the app to a Live.changed; left None everywhere else, so the
        # store keeps working with nobody listening.
        self.on_change: Callable[[], None] | None = None

    async def open(self) -> None:
        self._db = await aiosqlite.connect(self._path)
        try:
            self._db.row_factory = aiosqlite.Row
            await self._db.executescript(_SCHEMA)
            await self._migrate()
            await self._db.commit()
        except Exception:
            # A connection runs a thread; leaving it open on a failed start
            # turns a clear schema error into a process that will not exit.
            await self._db.close()
            self._db = None
            raise

    async def _migrate(self) -> None:
        """Columns added after a ledger already exists.

        CREATE TABLE IF NOT EXISTS does nothing to a table that is already
        there, so a running deployment would keep the old shape and every
        INSERT naming a new column would fail.
        """
        cursor = await self._db.execute("PRAGMA table_info(judgements)")  # type: ignore[union-attr]
        have = {row[1] for row in await cursor.fetchall()}
        typed = {
            "rule_key": "TEXT NOT NULL DEFAULT ''",
            "level": "TEXT NOT NULL DEFAULT ''",
            # The operator's ruling on a platform-vs-judge disagreement. The
            # importance is the label; the source says whose answer it agreed
            # with (platform / judge / operator), which the export keeps as
            # provenance in the note.
            "label_importance": "TEXT NOT NULL DEFAULT ''",
            "label_source": "TEXT NOT NULL DEFAULT ''",
            "labeled_at": "REAL",
            # Cross-alert correlation, v1: same upstream origin, multiple
            # rules, one window -> one burst id.
            "burst_id": "TEXT NOT NULL DEFAULT ''",
        }
        for column, ddl in typed.items():
            if column not in have:
                await self._db.execute(  # type: ignore[union-attr]
                    f"ALTER TABLE judgements ADD COLUMN {column} {ddl}"  # nosec B608
                )

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Store.open() was not called")
        return self._db

    async def prior_verdict(
        self, identity: str, window_seconds: int, now: float, *, any_route: bool = False
    ) -> dict[str, Any] | None:
        """The last judgement for this identity inside the window.

        For a storm, only AI verdicts are reusable: reusing a rule verdict
        would spread one degraded answer across the whole storm, and reusing a
        reuse would let a single judgement live forever by being re-served.

        For a RECOVERY (any_route=True) the goal is different. It is not saving
        a call — it is not contradicting the alert this recovery belongs to. A
        firing judged "high" by the rule floor must not end with a "medium"
        recovery card, so the recovery inherits whatever its firing said,
        degraded or not.
        """
        route_clause = "" if any_route else " AND route = 'ai'"
        # `route_clause` is one of two literals; the values are parametrized.
        cursor = await self.db.execute(
            "SELECT summary, importance, event_type, impact_scope, model FROM judgements"  # nosec B608
            f" WHERE identity = ?{route_clause} AND received_at >= ? ORDER BY id DESC LIMIT 1",
            (identity, now - max(1, window_seconds)),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def prior_rule_verdict(
        self, rule_key: str, level: str, window_seconds: int, now: float
    ) -> dict[str, Any] | None:
        """The last AI verdict for this alert rule at this level, inside the window.

        Only route='ai': reusing a rule-floor verdict would spread one degraded
        answer across a whole rule, which is not hypothetical — the same
        shortcut in WebhookWise filed 73 payment alerts as low while the model
        called every one of them high.

        Level has to match. Identity deliberately ignores severity so that an
        escalation stays one condition, which is right for a storm and wrong
        here: a rule that fired warning yesterday and critical today is asking
        a different question and must reach the model.
        """
        if not rule_key or window_seconds <= 0:
            return None
        cursor = await self.db.execute(
            "SELECT summary, importance, event_type, impact_scope, model FROM judgements"
            " WHERE rule_key = ? AND level = ? AND route = 'ai' AND is_recovery = 0 AND received_at >= ?"
            " ORDER BY id DESC LIMIT 1",
            (rule_key, level, now - max(1, window_seconds)),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def record(self, event: Any, verdict: Any, latency_ms: int) -> int:
        burst = await self._burst_id_for(event)
        cursor = await self.db.execute(
            "INSERT INTO judgements (received_at, source, identity, rule_key, level, correlation_id, title, body,"
            " fields_json, is_recovery, summary, importance, event_type, impact_scope, route, degraded_reason, model,"
            " tokens_in, tokens_out, cost, latency_ms, burst_id)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                event.received_at,
                event.source,
                event.identity,
                event.rule_key,
                event.level,
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
                burst,
            ),
        )
        await self.db.commit()
        self._announce()
        return int(cursor.lastrowid or 0)

    # A burst is DIFFERENT rules from one upstream origin inside one window —
    # the shape of a cascading incident. Same-rule repeats are already the
    # reuse route's business; a burst is the cross-alert layer above it.
    BURST_WINDOW_SECONDS = 600

    @staticmethod
    def _origin_of(fields: dict[str, Any], source: str) -> str:
        return str(fields.get("origin") or source or "")

    async def _burst_id_for(self, event: Any) -> str:
        origin = self._origin_of(event.fields, event.source)
        if not origin or event.is_recovery:
            return ""
        cutoff = float(event.received_at) - self.BURST_WINDOW_SECONDS
        cursor = await self.db.execute(
            "SELECT id, rule_key, burst_id, fields_json, source FROM judgements"
            " WHERE received_at >= ? AND is_recovery = 0 ORDER BY id DESC LIMIT 50",
            (cutoff,),
        )
        peers = []
        for row in await cursor.fetchall():
            try:
                fields = json.loads(row["fields_json"] or "{}")
            except ValueError:
                fields = {}
            if self._origin_of(fields, str(row["source"])) == origin and str(row["rule_key"]) != event.rule_key:
                peers.append(row)
        if not peers:
            return ""
        existing = next((str(row["burst_id"]) for row in peers if row["burst_id"]), "")
        burst = existing or f"burst-{int(event.received_at)}"
        # The FIRST member of a burst was recorded before anyone knew it was
        # one; joining it retroactively is what makes the group queryable.
        stragglers = [int(row["id"]) for row in peers if not row["burst_id"]]
        if stragglers:
            await self.db.execute(
                f"UPDATE judgements SET burst_id = ? WHERE id IN ({','.join('?' * len(stragglers))})",  # nosec B608
                (burst, *stragglers),
            )
        return burst

    # ── the operator's rulings on disagreements ──────────────────────────────

    async def disagreements(self, limit: int = 50) -> list[dict[str, Any]]:
        """Unlabeled rows where the platform and the judge picked different
        importances — the queue the review page drains, newest first."""
        vocab = ("critical", "high", "medium", "low")
        cursor = await self.db.execute(
            "SELECT * FROM judgements WHERE label_importance = ''"
            " AND level != '' AND importance != '' AND level != importance"
            f" AND level IN ({','.join('?' * len(vocab))}) ORDER BY id DESC LIMIT ?",  # nosec B608
            (*vocab, max(1, min(200, limit))),
        )
        return [dict(row) for row in await cursor.fetchall()]

    async def set_label(self, judgement_id: int, importance: str, source: str, now: float) -> bool:
        cursor = await self.db.execute(
            "UPDATE judgements SET label_importance = ?, label_source = ?, labeled_at = ? WHERE id = ?",
            (importance, source, now, judgement_id),
        )
        await self.db.commit()
        self._announce()
        return cursor.rowcount > 0

    async def labeled(self, limit: int = 1000) -> list[dict[str, Any]]:
        cursor = await self.db.execute(
            "SELECT * FROM judgements WHERE label_importance != '' ORDER BY id ASC LIMIT ?",
            (max(1, min(5000, limit)),),
        )
        return [dict(row) for row in await cursor.fetchall()]

    async def pending_returns(self, limit: int = 50) -> list[dict[str, Any]]:
        cursor = await self.db.execute(
            "SELECT * FROM judgements WHERE return_status = 'queued' ORDER BY id LIMIT ?", (limit,)
        )
        return [dict(row) for row in await cursor.fetchall()]

    def _announce(self) -> None:
        """Tell whoever is watching the board that its contents moved.

        A plain callback rather than an import: the store stays free of the
        HTTP layer, and a test can hand it a list.
        """
        if self.on_change is not None:
            self.on_change()

    async def mark_return(self, row_id: int, status: str, attempts: int, error: str | None) -> None:
        await self.db.execute(
            "UPDATE judgements SET return_status = ?, return_attempts = ?, return_error = ? WHERE id = ?",
            (status, attempts, (error or "")[:400] or None, row_id),
        )
        await self.db.commit()
        self._announce()

    async def recent(self, limit: int = 50, *, route: str | None = None, q: str | None = None) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if route:
            clauses.append("route = ?")
            params.append(route)
        if q:
            clauses.append("(title LIKE ? OR summary LIKE ?)")
            params += [f"%{q}%", f"%{q}%"]
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(max(1, min(500, limit)))
        # `where` is built from constant clause fragments; values are parametrized.
        cursor = await self.db.execute(
            f"SELECT * FROM judgements{where} ORDER BY id DESC LIMIT ?",  # nosec B608
            tuple(params),
        )
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
        # The shadow run's actual product: judge-vs-platform disagreement,
        # from two columns every row already carries (level = the upstream
        # platform's verdict as the pipe delivered it, importance = ours).
        # Recoveries are excluded: their importance is REUSED from the firing
        # by design, so counting them would manufacture agreement the judge
        # never expressed.
        cursor = await self.db.execute(
            "SELECT level, importance, count(*) AS n FROM judgements"
            " WHERE received_at >= ? AND level != '' AND importance != ''"
            "   AND coalesce(is_recovery, 0) = 0"
            " GROUP BY level, importance",
            (since,),
        )
        matrix: dict[str, dict[str, int]] = {}
        compared = agree = 0
        for row in await cursor.fetchall():
            lvl, imp, n = str(row["level"]), str(row["importance"]), int(row["n"])
            matrix.setdefault(lvl, {})[imp] = n
            compared += n
            if lvl == imp:
                agree += n
        cursor = await self.db.execute(
            "SELECT id, title, level, importance, route FROM judgements"
            " WHERE received_at >= ? AND level != '' AND importance != ''"
            "   AND coalesce(is_recovery, 0) = 0 AND level != importance"
            " ORDER BY id DESC LIMIT 5",
            (since,),
        )
        disagreements = [dict(row) for row in await cursor.fetchall()]
        return {
            "judged": judged,
            "routes": routes,
            "returns": returns,
            "cost": round(sum(r["cost"] for r in routes.values()), 6),
            # The number the cost conversation actually turns on: how many of
            # the judgements we served did we have to pay a model for?
            "paid_ratio_pct": round(100.0 * paid / judged, 1) if judged else 0.0,
            "agreement": {
                "compared": compared,
                "agree_pct": round(100.0 * agree / compared, 1) if compared else None,
                # {platform_level: {judge_importance: count}}
                "matrix": matrix,
                "recent_disagreements": disagreements,
            },
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
