"""The ledger: one row per judged event.

Same promise as the pipe's, narrowed to this service's one job — every event
that arrived has a verdict on record, with WHICH route produced it, what it
cost, and whether the result made it back. Reuse reads from this table too, so
the memory and the account are the same thing rather than a cache beside a log.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from typing import Any

import aiosqlite

from hookjudge.contract import COMPARABLE_LEVELS, LEVEL_SYNONYMS, platform_importance

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

-- A model's retrospective ruling on a CONDITION, and deliberately not a column
-- on judgements. Three reasons, in order of how much they would cost to undo:
--
--   1. `mattered` means a person said so. It is the only field in this ledger
--      that does, `ruled` counts it, and `mattered_pct` divides by it. An AI
--      answer sharing that column would make every one of those numbers a lie
--      that could not be untangled afterwards.
--   2. The unit is different. A person rules on the card that woke them; a model
--      reading twenty case files rules on the condition behind them. Writing the
--      second onto one row would pick an arbitrary card to carry it.
--   3. It is cheap. Fifteen conditions, not a hundred and sixty-five cards.
--
-- `why` is required by the writer, not by SQLite: a verdict with no evidence is
-- an opinion, and this table exists to be argued with.
CREATE TABLE IF NOT EXISTS ai_rulings (
    identity TEXT PRIMARY KEY,
    verdict TEXT NOT NULL,
    why TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    at REAL NOT NULL
);
"""

# Indexes over a column _migrate ADDS, so they cannot live in _SCHEMA: that runs
# first, against a ledger which may still have the old shape, and an index on a
# column that does not exist yet is an error — the service would fail to open its
# own database.
_INDEXES = """
CREATE INDEX IF NOT EXISTS ix_burst ON judgements (origin, received_at);
"""


def _comparable_level_sql() -> tuple[str, tuple[str, ...]]:
    """`level` said in the judge's four words, as a SQL expression.

    One CASE arm per documented synonym, with the values parametrized. Generated
    from contract.LEVEL_SYNONYMS rather than written out here, because the whole
    defect this repairs was the mapping existing in one place (the rule floor)
    and being re-implemented by absence in two others.
    """
    arms = "".join(" WHEN ? THEN ?" for _ in LEVEL_SYNONYMS)
    values = tuple(value for pair in LEVEL_SYNONYMS.items() for value in pair)
    return f"CASE level{arms} ELSE level END", values


# A row is BILLED when tokens were actually consumed, whatever route judged it.
# The two are not the same thing and that is the point: a provider answering
# prose instead of JSON still charges for the attempt, so those alerts land on
# the `rule` route carrying a real cost. Counting by route alone reported them
# as free rule verdicts and the spend appeared nowhere.
_BILLED_SQL = "sum(CASE WHEN tokens_in > 0 OR tokens_out > 0 THEN 1 ELSE 0 END)"


class Store:
    def __init__(self, path: str, *, burst_window_seconds: int = 600) -> None:
        self._path = path
        self._db: aiosqlite.Connection | None = None
        self._burst_window_seconds = burst_window_seconds
        # Every write goes through this. One connection is shared by the ingest
        # tasks, the return worker and the purge, and sqlite3's implicit
        # transaction is per-CONNECTION, not per-caller — so a commit() anywhere
        # publishes whatever any other task has half-written. The lock is what
        # makes "these statements are one fact" true; see record().
        self._write = asyncio.Lock()
        # Set by the app to a Live.changed; left None everywhere else, so the
        # store keeps working with nobody listening.
        self.on_change: Callable[[], None] | None = None

    async def open(self) -> None:
        self._db = await aiosqlite.connect(self._path)
        try:
            self._db.row_factory = aiosqlite.Row
            # WAL buys concurrent readers: /status, /metrics and every board
            # refetch read this file while verdicts are being written to it, and
            # under the default rollback journal a reader and the writer lock
            # each other out. busy_timeout buys patience when two writers do
            # collide — without it sqlite fails the statement instantly with
            # "database is locked", which on this connection meant a lost
            # attempt count on the return leg or a lost alert that had already
            # been answered 202.
            await self._db.execute("PRAGMA journal_mode=WAL")
            await self._db.execute("PRAGMA busy_timeout=5000")
            await self._db.executescript(_SCHEMA)
            await self._migrate()
            await self._db.executescript(_INDEXES)
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
            # The judge's own second axis: 'yes' | 'no' | '' when it did not
            # answer. Beside `mattered` and never merged with it — that one means
            # a person spoke, this one means the model did. A board that showed
            # them as one number would be unable to say which.
            "wake_someone": "TEXT NOT NULL DEFAULT ''",
            "mattered": "TEXT NOT NULL DEFAULT ''",
            "mattered_at": "REAL",
            # The opaque IM user id the pipe passed through, when it had one.
            # The judge cannot resolve it to a person and does not try; it is
            # provenance, the way label_source is.
            "mattered_actor": "TEXT NOT NULL DEFAULT ''",
            # The upstream system an alert came FROM (fields.origin, else
            # source), resolved once at write time. Burst grouping used to read
            # it back out of fields_json in Python, which is exactly why it
            # could not filter on it in SQL — see _burst_id_for. Not backfilled:
            # grouping only ever looks one window back, so the cost of leaving
            # history blank is that the ten minutes either side of a restart
            # cannot form a burst, and the cost of a backfill is parsing every
            # row in the ledger before the service will answer at all.
            "origin": "TEXT NOT NULL DEFAULT ''",
            # When the return leg last TRIED, as opposed to when the alert
            # arrived. NULL on rows queued before this column existed, and the
            # worker falls back to received_at for those.
            "return_attempted_at": "REAL",
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
        # The burst UPDATE and this INSERT are ONE fact — "these rows are the
        # same incident, and here is the member that proved it" — and they were
        # two statements with no transaction of their own. On a shared
        # connection, any other task's commit() landing between them published
        # the UPDATE alone; if the INSERT then failed, the ledger held a burst
        # whose triggering member is missing from it. The lock is the transaction.
        origin = self._origin_of(event.fields, event.source)
        async with self._write:
            burst = await self._burst_id_for(event, origin)
            cursor = await self.db.execute(
                "INSERT INTO judgements (received_at, source, origin, identity, rule_key, level, correlation_id,"
                " title, body, fields_json, is_recovery, summary, importance, event_type, impact_scope,"
                " wake_someone, route,"
                " degraded_reason, model, tokens_in, tokens_out, cost, latency_ms, burst_id)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    event.received_at,
                    event.source,
                    origin,
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
                    verdict.wake_someone,
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

    @staticmethod
    def _origin_of(fields: dict[str, Any], source: str) -> str:
        return str(fields.get("origin") or source or "")

    # A burst is DIFFERENT rules from one upstream origin inside one window —
    # the shape of a cascading incident. Same-rule repeats are already the
    # reuse route's business; a burst is the cross-alert layer above it.
    async def _burst_id_for(self, event: Any, origin: str) -> str:
        """Called with the write lock held; see record()."""
        if not origin or event.is_recovery:
            return ""
        cutoff = float(event.received_at) - max(1, self._burst_window_seconds)
        # Both filters are IN the query now. The origin match used to run in
        # Python over "the last 50 rows in the window", so 120 unrelated alerts
        # from other systems inside those ten minutes hid every peer this alert
        # had: two rules from one origin came out with no burst_id at all, and
        # cross-alert grouping stopped working on exactly the busy ledger it
        # exists for. LIMIT 50 stays as a cap on a genuine storm — it just caps
        # the peers now instead of the search.
        cursor = await self.db.execute(
            "SELECT id, burst_id FROM judgements"
            " WHERE origin = ? AND rule_key != ? AND received_at >= ? AND is_recovery = 0"
            " ORDER BY id DESC LIMIT 50",
            (origin, event.rule_key, cutoff),
        )
        peers = await cursor.fetchall()
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
        importances — the queue the review page drains, newest first.

        `warning` is admitted, and compared as the `medium` it means. It was
        excluded from the vocabulary while ALSO being scored as a disagreement by
        the agreement matrix, which is the worst of both: the most common
        severity Prometheus and Grafana emit was counted against the judge on
        /status and then could not be brought up for review to be corrected.
        """
        level_sql, level_params = _comparable_level_sql()
        # `level_sql` is CASE arms generated from a constant map; every value,
        # including the arms', is parametrized.
        cursor = await self.db.execute(
            "SELECT * FROM judgements WHERE label_importance = ''"  # nosec B608
            f" AND level != '' AND importance != '' AND {level_sql} != importance"
            f" AND level IN ({','.join('?' * len(COMPARABLE_LEVELS))}) ORDER BY id DESC LIMIT ?",
            (*level_params, *COMPARABLE_LEVELS, max(1, min(200, limit))),
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

    async def mark_return(
        self, row_id: int, status: str, attempts: int, error: str | None, *, attempted_at: float | None = None
    ) -> None:
        """The return leg's state after one attempt, attempt clock included.

        return_attempted_at was added to the schema for the worker's backoff and
        then never written, so the column existed, its comment promised the
        worker used it, and the backoff went on measuring from received_at. It is
        set on every call — including the caller passing None, which CLEARS it,
        because a caller putting a row back to `queued` by hand is saying "try
        this now" and a stale clock would make the worker wait out a delay for an
        attempt that no longer happened.
        """
        await self.db.execute(
            "UPDATE judgements SET return_status = ?, return_attempts = ?, return_error = ?,"
            " return_attempted_at = ? WHERE id = ?",
            (status, attempts, (error or "")[:400] or None, attempted_at, row_id),
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

    # A firing that resolves itself this fast, with nobody having touched it, is
    # a threshold flapping rather than an event. Not a hard truth — a real
    # incident can self-heal in four minutes — which is why it is reported as its
    # own number and never folded into `mattered`.
    SELF_HEAL_FAST_SECONDS = 10 * 60

    AI_VERDICTS = ("worth_it", "not_worth_it")

    async def record_ai_ruling(self, identity: str, *, verdict: str, why: str, model: str, at: float) -> dict[str, Any]:
        """A model's retrospective ruling on one condition. Latest wins.

        Latest wins rather than first, which is the opposite of `record_mattered`
        — a person's ruling is a fact about a moment and must not be overwritten
        by a later press, while this is a standing read of the evidence and the
        evidence keeps arriving. A condition that stopped self-resolving should
        be re-ruled, not defended by its own history.

        Refuses an unknown verdict and an empty reason. The reason is the whole
        point: `likely_flapping` already says something true with no words, so a
        model that cannot say WHY it disagrees with a human's absence is adding
        confidence rather than information.
        """
        if verdict not in self.AI_VERDICTS:
            raise ValueError(f"verdict must be one of {self.AI_VERDICTS}, not {verdict!r}")
        if not why.strip():
            raise ValueError("an AI ruling without a reason is an opinion; say why")
        await self.db.execute(
            "INSERT INTO ai_rulings (identity, verdict, why, model, at) VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(identity) DO UPDATE SET verdict = excluded.verdict, why = excluded.why,"
            " model = excluded.model, at = excluded.at",
            (identity, verdict, why.strip()[:600], model[:80], at),
        )
        await self.db.commit()
        return {"identity": identity, "verdict": verdict}

    async def ai_rulings(self, since: float | None = None) -> dict[str, dict[str, Any]]:
        """Standing AI rulings, by identity, for conditions seen since `since`.

        A ruling itself is not an event in a window — it is a standing read of a
        condition, and re-reading it every week to keep it inside the window
        would be pointless. But the COUNT beside `ruled` has to answer the same
        question `ruled` does, and it did not: `ruled` counted presses inside the
        window while this counted every row in the table, including rulings on
        conditions that never fired here at all. Nothing validates that an
        identity exists in the ledger, so an orphan row inflated the total while
        appearing in no row of `noisiest`.

        Two numbers side by side with different denominators is the exact defect
        this ledger spent a week removing from its own board. So the ruling is
        kept standing and the count is joined to the window.
        """
        if since is None:
            cursor = await self.db.execute("SELECT identity, verdict, why, model, at FROM ai_rulings")
        else:
            cursor = await self.db.execute(
                "SELECT r.identity, r.verdict, r.why, r.model, r.at FROM ai_rulings r"
                " WHERE EXISTS (SELECT 1 FROM judgements j"
                " WHERE j.identity = r.identity AND j.received_at >= ?)",
                (since,),
            )
        return {
            str(row["identity"]): {
                "verdict": str(row["verdict"]),
                "why": str(row["why"]),
                "model": str(row["model"]),
                "at": float(row["at"]),
            }
            for row in await cursor.fetchall()
        }

    async def self_healing(self, since: float) -> dict[str, dict[str, Any]]:
        """Per condition: how often it resolved itself, and how quickly.

        The signal that needs no human. The ruling columns answer "was this worth
        an interruption" and they are empty until somebody presses a button — and
        on an unattended deployment nobody does, which leaves the attention
        figures permanently uncalibrated. This is the proxy available from data
        the ledger already keeps: pair each firing with the recovery that
        followed it and look at the gap.

        It is a PROXY and is labelled as one everywhere it surfaces. A human
        saying "not worth waking me" is evidence; a condition healing in five
        minutes is a strong hint that points at the same place. Presenting the
        second as the first would be the ledger claiming somebody spoke when
        nobody did.

        Pairing is done here rather than in SQL because it is inherently
        sequential — a firing is closed by the NEXT recovery of the same
        identity, and a second firing before that recovery is the same open
        condition restated, not a new one to pair. Window functions can express
        that; a readable loop over one window's rows can too, and this one has to
        be read by whoever doubts the number.
        """
        cursor = await self.db.execute(
            "SELECT identity, is_recovery, received_at FROM judgements WHERE received_at >= ? ORDER BY received_at, id",
            (since,),
        )
        opened: dict[str, float] = {}
        spans: dict[str, list[float]] = {}
        fired: dict[str, int] = {}
        for row in await cursor.fetchall():
            identity = str(row["identity"])
            spans.setdefault(identity, [])
            if row["is_recovery"]:
                start = opened.pop(identity, None)
                if start is not None:
                    spans[identity].append(max(0.0, float(row["received_at"]) - start))
            else:
                fired[identity] = fired.get(identity, 0) + 1
                # setdefault, not assignment: a restatement while the condition
                # is still open must not move the clock, or a storm would look
                # like it healed in the gap between its last two cards.
                opened.setdefault(identity, float(row["received_at"]))
        out: dict[str, dict[str, Any]] = {}
        for identity, gaps in spans.items():
            ordered = sorted(gaps)
            median = ordered[len(ordered) // 2] if ordered else None
            out[identity] = {
                "fired": fired.get(identity, 0),
                "self_resolved": len(ordered),
                "median_seconds": round(median, 1) if median is not None else None,
                # The one-line read: it healed itself, fast, more often than not.
                "likely_flapping": bool(
                    ordered
                    and median is not None
                    and median <= self.SELF_HEAL_FAST_SECONDS
                    and len(ordered) * 2 >= fired.get(identity, 0)
                ),
            }
        return out

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
            " coalesce(sum(mattered = 'no'), 0) AS did_not_matter,"
            " coalesce(sum(wake_someone = 'yes'), 0) AS wake_yes,"
            " coalesce(sum(wake_someone = 'no'), 0) AS wake_no"
            " FROM judgements WHERE received_at >= ?",
            (since,),
        )
        totals = await cursor.fetchone()
        interruptions = int(totals["n"]) if totals else 0
        conditions = int(totals["conditions"]) if totals else 0
        mattered = int(totals["mattered"]) if totals else 0
        did_not_matter = int(totals["did_not_matter"]) if totals else 0
        ruled = mattered + did_not_matter
        wake_yes = int(totals["wake_yes"]) if totals else 0
        wake_no = int(totals["wake_no"]) if totals else 0
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
        healing = await self.self_healing(since)
        ai = await self.ai_rulings(since)
        noisiest = [
            {
                "identity": str(row["identity"]),
                "title": str(row["title"]),
                "interruptions": int(row["n"]),
                # The contrast, on one line: twelve interruptions, one paid for.
                "paid": int(row["paid"]),
                "mattered": int(row["mattered"]),
                "did_not_matter": int(row["did_not_matter"]),
                # The unattended half of the same question. Kept as its own keys
                # so nothing here can be mistaken for something a human said.
                #
                # `fired` is here because without it this row cannot be checked.
                # `interruptions` counts every card, recoveries included;
                # `self_resolved` counts closed episodes; and `likely_flapping`
                # divides episodes by FIRINGS. So a reader who divides the two
                # visible numbers gets 30/77 and concludes the flag is broken,
                # when the comparison it actually made was 30/47. Showing the
                # denominator costs one integer and makes the verdict auditable
                # by the person most likely to doubt it.
                **{
                    key: (healing.get(str(row["identity"])) or {}).get(key)
                    for key in ("fired", "self_resolved", "median_seconds", "likely_flapping")
                },
                # The third epistemic state on this row, and the reason all three
                # are separate keys: `mattered` is what a person said,
                # `likely_flapping` is what the behaviour shows, `ai_ruling` is
                # what a model concluded from the case files. They can disagree,
                # and a row where they do is the most interesting row here.
                "ai_ruling": (ai.get(str(row["identity"])) or {}).get("verdict"),
                "ai_why": (ai.get(str(row["identity"])) or {}).get("why"),
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
            # How many conditions look like flapping on the evidence alone. This
            # is the number that stays useful when `ruled` never moves, which on
            # an unattended deployment is the normal case rather than a failure.
            "likely_flapping": sum(1 for row in healing.values() if row["likely_flapping"]),
            # Counted apart from `ruled` for the same reason it is stored apart:
            # `ruled` answers "how many times did a person tell us", and an AI
            # ruling folded into it would answer nothing at all. Same WINDOW as
            # `ruled` though — see ai_rulings() for why that took a fix.
            # The judge's own second axis, and the number that decides whether
            # asking it was worth anything. `importance` came back 'high' for 210
            # of 216, which is a classifier agreeing with itself. If this one also
            # answers 'yes' almost always, it is the same non-answer wearing a
            # different field name, and the honest response is to stop paying for
            # it — so it is counted where that is obvious, not averaged away.
            "wake_yes": wake_yes,
            "wake_no": wake_no,
            "wake_answered": wake_yes + wake_no,
            "ai_ruled": len(ai),
            "ai_not_worth_it": sum(1 for row in ai.values() if row["verdict"] == "not_worth_it"),
            "noisiest": noisiest,
        }

    async def summary(self, since: float) -> dict[str, Any]:
        cursor = await self.db.execute(
            "SELECT route, count(*) AS n, coalesce(sum(cost),0) AS cost, coalesce(avg(latency_ms),0) AS latency,"
            f" {_BILLED_SQL} AS billed"  # nosec B608 — a module constant, no caller input reaches it
            " FROM judgements WHERE received_at >= ? GROUP BY route",
            (since,),
        )
        by_route = await cursor.fetchall()
        routes = {
            str(row["route"]): {
                "count": int(row["n"]),
                "cost": round(float(row["cost"]), 6),
                "avg_latency_ms": int(row["latency"]),
                # Rows on THIS route that a provider actually charged for. On
                # `ai` it equals count; anywhere else a non-zero number is a
                # degraded answer that still cost money.
                "billed": int(row["billed"] or 0),
            }
            for row in by_route
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
            # The matrix keeps the platform's own word as the key — `warning` is
            # what it said — while agreement is scored on what that word MEANS.
            # Comparing the strings raw made every `warning` row a disagreement
            # with a judge that had said the same thing, and `warning` is the
            # majority of what these platforms send.
            matrix.setdefault(lvl, {})[imp] = n
            compared += n
            if platform_importance(lvl) == imp:
                agree += n
        level_sql, level_params = _comparable_level_sql()
        # `level_sql` is CASE arms generated from a constant map; the values are
        # parametrized.
        cursor = await self.db.execute(
            "SELECT id, title, level, importance, route FROM judgements"  # nosec B608
            " WHERE received_at >= ? AND level != '' AND importance != ''"
            f"   AND coalesce(is_recovery, 0) = 0 AND {level_sql} != importance"
            " ORDER BY id DESC LIMIT 5",
            (since, *level_params),
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
