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
            # A person's ruling on whether THIS interruption was worth it:
            # 'yes' | 'no' | '' for unruled. A different axis from
            # label_importance, which answers what importance the alert should
            # have had — an alert correctly rated `high` can still not be worth
            # waking anyone at 3am. Both live on the row and neither overwrites
            # the other, because both answers are true at once.
            "mattered": "TEXT NOT NULL DEFAULT ''",
            "mattered_at": "REAL",
            # The opaque IM user id the pipe passed through, when it had one.
            # The judge cannot resolve it to a person and does not try; it is
            # provenance, the way label_source is.
            "mattered_actor": "TEXT NOT NULL DEFAULT ''",
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

    # ── the human's ruling on whether the interruption was worth it ───────────

    async def _by_correlation(self, correlation_id: str, event_id: str = "") -> dict[str, Any] | None:
        """The judgement a card was made from, newest first.

        Newest because a delivery the pipe retried is two rows carrying one
        correlation id, and the ruling belongs to the card the operator was
        actually looking at — the last one sent.

        The hr-<event_id> fallback is the same precedence Incoming.parse uses on
        the way in: the flat wire shape carries no correlation id at all, so
        that convention is what the row ended up stamped with.
        """
        candidates = [correlation_id.strip()]
        if str(event_id).strip():
            candidates.append(f"hr-{str(event_id).strip()}")
        for candidate in candidates:
            if not candidate:
                continue
            cursor = await self.db.execute(
                "SELECT * FROM judgements WHERE correlation_id = ? ORDER BY id DESC LIMIT 1", (candidate,)
            )
            row = await cursor.fetchone()
            if row:
                return dict(row)
        return None

    async def record_mattered(
        self, correlation_id: str, *, mattered: str, at: float, actor: str = "", event_id: str = ""
    ) -> dict[str, Any] | None:
        """One person's ruling on one interruption, against the judgement that caused it.

        Deliberately not label_importance. Every row carrying that column
        becomes an eval row in /labels/export, so writing 'yes' into it would
        emit expect.importance="yes" into the eval set, and /disagreements
        selects on label_importance = '' — a button press in a chat window
        would have quietly drained the review queue of a row nobody reviewed.

        Idempotent by (kind, at): a redelivered press finds its own ruling
        already on the row and changes nothing. A press OLDER than the ruling on
        record is dropped rather than applied, which is the same defect from the
        other side — an operator who pressed "not worth it" and then changed
        their mind must not have the first press reinstated by a retry that
        arrived late.
        """
        row = await self._by_correlation(correlation_id, event_id)
        if row is None:
            return None
        row_id = int(row["id"])
        standing = str(row["mattered"] or "")
        stood_at = None if row["mattered_at"] is None else float(row["mattered_at"])
        if stood_at is not None and (at < stood_at or (at == stood_at and standing == mattered)):
            return {"id": row_id, "mattered": standing, "applied": False}
        await self.db.execute(
            "UPDATE judgements SET mattered = ?, mattered_at = ?, mattered_actor = ? WHERE id = ?",
            (mattered, at, actor[:120], row_id),
        )
        await self.db.commit()
        self._announce()
        return {"id": row_id, "mattered": mattered, "applied": True}

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

    # The noisiest conditions, capped. The cap is not cosmetic: this list is
    # also emitted as Prometheus labels, and an alert identity as a label value
    # is unbounded cardinality — the classic way to take a metrics store down.
    # Five is the same depth `recent_disagreements` shows, for the same reason:
    # a board's job is to name where to GO, not to be the report.
    NOISIEST_LIMIT = 5

    async def attention(self, since: float, *, limit: int | None = None) -> dict[str, Any]:
        """The bill the cost figures cannot show: how often a human was INTERRUPTED.

        `interruptions` is the same number as `judged`, and that identity is the
        finding rather than a redundancy. Every judgement is returned to the pipe
        and becomes a card, so the ledger's headline count has always BEEN the
        number of times somebody was interrupted; it was only ever read as
        throughput. A condition judged twelve times inside an hour interrupted a
        human twelve times, and "$0.004 spend" — eleven of those twelve being
        free `reuse` routes — says nothing whatsoever about that. `repeats` is
        the part that was invisible: cards that restated a condition the operator
        had already been told about in this window.

        Nothing here changes what is returned. Who owns noise when a verdict is
        reused is deliberately still open (the proposed note of 2026-08-12), and
        this closes none of it — it makes the bill for attention legible so that
        decision can eventually be taken on evidence instead of taste.
        """
        cursor = await self.db.execute(
            "SELECT count(*) AS n, count(DISTINCT identity) AS conditions,"
            " coalesce(sum(mattered = 'yes'), 0) AS mattered,"
            " coalesce(sum(mattered = 'no'), 0) AS did_not_matter"
            " FROM judgements WHERE received_at >= ?",
            (since,),
        )
        totals = await cursor.fetchone()
        interruptions = int(totals["n"]) if totals else 0
        conditions = int(totals["conditions"]) if totals else 0
        mattered = int(totals["mattered"]) if totals else 0
        did_not_matter = int(totals["did_not_matter"]) if totals else 0
        ruled = mattered + did_not_matter
        # A condition that interrupted once is not noise, so the view that is
        # meant to say "go turn something off" refuses to pad itself with
        # one-offs. `title` is a bare column beside max(id): SQLite documents it
        # as coming from the row the max was found on, which is the most recent
        # wording of a condition whose title may have been decorated since.
        cursor = await self.db.execute(
            "SELECT identity, title, max(id) AS last_id, max(received_at) AS last_seen, count(*) AS n,"
            " coalesce(sum(route = 'ai'), 0) AS paid,"
            " coalesce(sum(mattered = 'yes'), 0) AS mattered,"
            " coalesce(sum(mattered = 'no'), 0) AS did_not_matter"
            " FROM judgements WHERE received_at >= ? GROUP BY identity HAVING count(*) > 1"
            " ORDER BY n DESC, last_id DESC LIMIT ?",
            (since, max(1, min(50, limit or self.NOISIEST_LIMIT))),
        )
        noisiest = [
            {
                "identity": str(row["identity"]),
                "title": str(row["title"]),
                "interruptions": int(row["n"]),
                # The contrast, on one line: twelve interruptions, one paid for.
                "paid": int(row["paid"]),
                "mattered": int(row["mattered"]),
                "did_not_matter": int(row["did_not_matter"]),
                "last_seen": float(row["last_seen"]),
            }
            for row in await cursor.fetchall()
        ]
        return {
            "interruptions": interruptions,
            "conditions": conditions,
            "repeats": interruptions - conditions,
            "mattered": mattered,
            "did_not_matter": did_not_matter,
            "ruled": ruled,
            # Of the RULED ones, not of every interruption: an unruled card is
            # not evidence that it did not matter, and a percentage that treats
            # silence as a verdict flatters itself.
            "mattered_pct": round(100.0 * mattered / ruled, 1) if ruled else None,
            "noisiest": noisiest,
        }

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
            # And the number the cost conversation cannot answer at all: how
            # many times was somebody interrupted, and did any of it matter?
            # Nested here, beside `agreement`, because it is read off the same
            # rows in the same window.
            "attention": await self.attention(since),
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
