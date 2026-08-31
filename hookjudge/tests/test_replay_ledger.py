"""The ledger replay: what it picks, what it believes, and what it refuses to call a difference."""

from __future__ import annotations

import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from hookjudge.contract import Incoming, Verdict
from hookjudge.settings import Settings
from hookjudge.store import Store

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from replay_ledger import diff_row, moved, open_ledger, pick_rows, render, replay, summarize  # noqa: E402

NOW = time.time()


def _verdict(importance: str = "high", wake: str = "yes", *, route: str = "ai", degraded: str = "") -> Verdict:
    return Verdict(
        summary="one sentence",
        importance=importance,
        event_type="infrastructure",
        impact_scope="x",
        wake_someone=wake,
        route=route,
        degraded_reason=degraded,
        cost=0.001 if route == "ai" else 0.0,
        tokens_in=10 if route == "ai" else 0,
        tokens_out=5 if route == "ai" else 0,
        model="m-old" if route == "ai" else "",
    )


async def _fill(db_path: Path, specs: list[dict[str, Any]]) -> None:
    """Rows written through the real Store, so the test pins the real schema."""
    store = Store(str(db_path))
    await store.open()
    for spec in specs:
        event = Incoming.parse(
            {
                "source": spec.get("source", "grafana"),
                "title": spec["title"],
                "body": spec.get("body", ""),
                "level": spec.get("level", "warning"),
                "fields": spec.get("fields", {}),
            },
            now=NOW - spec.get("age", 0.0),
        )
        await store.record(event, spec["verdict"], latency_ms=1)
    await store.close()


def _mark(db_path: Path, where_body: str, **columns: Any) -> None:
    """The columns a person writes (label, mattered) and the recovery flag."""
    db = sqlite3.connect(str(db_path))
    sets = ", ".join(f"{name} = ?" for name in columns)
    db.execute(f"UPDATE judgements SET {sets} WHERE body = ?", (*columns.values(), where_body))  # noqa: S608
    db.commit()
    db.close()


def _scripted(answers: dict[str, list[Verdict]]) -> Any:
    """A judge that reads from a per-title script and counts its calls."""
    calls: Counter[str] = Counter()

    async def judge(client: Any, settings: Any, event: Any) -> Verdict:
        queue = answers[event.title]
        answer = queue[min(calls[event.title], len(queue) - 1)]
        calls[event.title] += 1
        return answer

    judge.calls = calls  # type: ignore[attr-defined]
    return judge


async def test_pick_rows_takes_the_latest_ai_firing_per_rule_and_ranks_by_all_route_volume(tmp_path: Path) -> None:
    db_path = tmp_path / "ledger.db"
    disk = {"fields": {"alertname": "DiskFull"}}
    await _fill(
        db_path,
        [
            {"title": "disk 91%", "body": "old", "age": 300, "verdict": _verdict(), **disk},
            {"title": "disk 93%", "body": "newest", "age": 100, "verdict": _verdict(), **disk},
            # Reuse rows are volume, not replay candidates: the paid verdict answered them.
            *(
                {"title": "disk again", "body": f"r{n}", "age": 90, "verdict": _verdict(route="reuse"), **disk}
                for n in range(5)
            ),
            {
                "title": "payment fail",
                "body": "pay",
                "age": 50,
                "verdict": _verdict("critical"),
                "fields": {"alertname": "PayFail"},
            },
            {"title": "floor only", "body": "floor", "age": 40, "verdict": _verdict(route="rule")},
            {"title": "disk recovered", "body": "recovery", "age": 10, "verdict": _verdict(), **disk},
        ],
    )
    _mark(db_path, "recovery", is_recovery=1)

    db = open_ledger(str(db_path))
    rows = pick_rows(db, since=NOW - 1000, per_rule=1, limit=0)
    db.close()

    assert [r["rule"] for r in rows] == ["DiskFull", "PayFail"], "floor and recovery rows are not candidates"
    assert rows[0]["body"] == "newest", "per-rule keeps the LATEST firing, not the first found"
    assert rows[0]["seen"] == 8, "volume counts every route: 3 ai + 5 reuse answered by this rule"

    db = open_ledger(str(db_path))
    capped = pick_rows(db, since=NOW - 1000, per_rule=1, limit=1)
    db.close()
    assert [r["rule"] for r in capped] == ["DiskFull"], "a cap keeps the loudest rule, where delivery goes"


async def test_the_window_is_a_wall(tmp_path: Path) -> None:
    db_path = tmp_path / "ledger.db"
    await _fill(
        db_path,
        [
            {"title": "ancient", "body": "a", "age": 5000, "verdict": _verdict()},
            {"title": "recent", "body": "b", "age": 10, "verdict": _verdict()},
        ],
    )
    db = open_ledger(str(db_path))
    rows = pick_rows(db, since=NOW - 1000, per_rule=1, limit=0)
    db.close()
    assert [r["title"] for r in rows] == ["recent"]


def test_moved_reads_only_the_axes_delivery_reads() -> None:
    row = {"importance": "high", "wake_someone": "yes"}
    assert not moved(row, _verdict("high", "yes"))
    assert moved(row, _verdict("medium", "yes"))
    assert moved(row, _verdict("high", "no"))
    assert not moved(row, _verdict("low", "no", degraded="AI call failed")), "a floor answer is not a move"
    assert not moved({"importance": "high", "wake_someone": ""}, _verdict("high", "no")), (
        "an unscored recorded wake cannot flip"
    )


async def test_a_steady_move_is_a_difference_and_only_movers_pay_extra_draws(tmp_path: Path) -> None:
    db_path = tmp_path / "ledger.db"
    await _fill(
        db_path,
        [
            {"title": "disk 93%", "verdict": _verdict("high", "yes"), "fields": {"alertname": "DiskFull"}},
            {"title": "cert expiring", "verdict": _verdict("medium", "no"), "fields": {"alertname": "CertSoon"}},
        ],
    )
    db = open_ledger(str(db_path))
    rows = pick_rows(db, since=NOW - 1000, per_rule=1, limit=0)
    db.close()

    judge = _scripted(
        {
            "disk 93%": [_verdict("medium", "no")],  # steady across every draw
            "cert expiring": [_verdict("medium", "no")],  # agrees with the record
        }
    )
    diffs = await replay(rows, Settings.load(), None, votes=3, concurrency=2, judge=judge)

    flipped = {d["rule"]: d for d in diffs if d["flip"]}
    assert set(flipped) == {"DiskFull"}
    assert flipped["DiskFull"]["delta"] == -1, "high -> medium is one step quieter"
    assert flipped["DiskFull"]["new_quiet"], "yes -> no is the axis the pipe drops cards on"
    assert not flipped["DiskFull"]["unsteady"]
    assert judge.calls["disk 93%"] == 3, "a mover is re-asked to a majority of 3"
    assert judge.calls["cert expiring"] == 1, "the bill only pays extra where the first draw moved"


async def test_a_coin_flip_is_the_candidate_disagreeing_with_itself(tmp_path: Path) -> None:
    db_path = tmp_path / "ledger.db"
    await _fill(db_path, [{"title": "disk 93%", "verdict": _verdict("high", "yes")}])
    db = open_ledger(str(db_path))
    rows = pick_rows(db, since=NOW - 1000, per_rule=1, limit=0)
    db.close()

    judge = _scripted({"disk 93%": [_verdict("medium", "yes"), _verdict("high", "yes"), _verdict("high", "yes")]})
    diffs = await replay(rows, Settings.load(), None, votes=3, concurrency=1, judge=judge)

    assert not diffs[0]["flip"], "the majority went back to the record"
    assert diffs[0]["unsteady"], "and the report says who actually disagreed: the candidate, with itself"


async def test_rows_a_person_ruled_on_lead_the_report(tmp_path: Path) -> None:
    db_path = tmp_path / "ledger.db"
    await _fill(db_path, [{"title": "pay fail", "body": "ruled", "verdict": _verdict("high", "yes")}])
    _mark(db_path, "ruled", label_importance="high", mattered="yes")
    db = open_ledger(str(db_path))
    rows = pick_rows(db, since=NOW - 1000, per_rule=1, limit=0)
    db.close()

    judge = _scripted({"pay fail": [_verdict("medium", "no")]})
    diffs = await replay(rows, Settings.load(), None, votes=3, concurrency=1, judge=judge)
    summary = summarize(diffs, model="m-new", window_days=30, votes=3)

    assert diffs[0]["label_delta"] == -1, "below what a PERSON ruled — the one comparison here that is a label"
    assert diffs[0]["mattered_quiet"], "a person said the interruption was worth it; the candidate drops the card"
    assert summary["label_below"] == 1
    assert summary["mattered_quiet"] == 1

    report = render(summary, diffs)
    assert "AGAINST A HUMAN RULING" in report
    assert report.index("AGAINST A HUMAN RULING") < report.index("confirmed difference"), "rulings lead"
    assert "Not a gate" in report, "the report says out loud what it must never become"


def test_degraded_draws_are_a_finding_about_the_candidate_not_a_difference() -> None:
    row = {
        "id": 1,
        "rule": "r",
        "seen": 1,
        "importance": "high",
        "wake_someone": "yes",
        "label_importance": "",
        "mattered": "",
    }
    diff = diff_row(row, [_verdict("low", "no", degraded="AI call failed: TimeoutException")])
    assert not diff["flip"]
    assert diff["degraded"] == 1
    summary = summarize([diff], model="m", window_days=30, votes=1)
    assert summary["degraded_draws"] == 1
    assert summary["flips"] == 0
