"""The labelling queue: what it orders by, and what it refuses to vote with."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from eval_queue import _key, _spread, build  # noqa: E402


def _opinions(name: str, **rows: str) -> dict[str, object]:
    return {"collected": True, "model": name, "rows": {k: {"importance": v} for k, v in rows.items()}}


def test_spread_measures_distance_not_disagreement_count() -> None:
    """Three judges split low/low/medium is a smaller problem than two split
    low/critical, and the queue has to say so or volume ordering does all the work."""
    assert _spread(["low", "low", "medium"]) == 1
    assert _spread(["low", "critical"]) == 3
    assert _spread(["high", "high", "high"]) == 0
    assert _spread(["high"]) == 0, "one opinion cannot disagree with itself"
    assert _spread([]) == 0


def test_the_widest_disagreement_comes_first_then_the_loudest_rule() -> None:
    dataset = [
        {"id": "quiet-contested", "seen": 1},
        {"id": "loud-agreed", "seen": 500},
        {"id": "loud-contested", "seen": 400},
    ]
    a = _opinions("a", **{"quiet-contested": "low", "loud-agreed": "high", "loud-contested": "low"})
    b = _opinions("b", **{"quiet-contested": "critical", "loud-agreed": "high", "loud-contested": "high"})

    rows = build(dataset, {"a": a, "b": b}, {})["rows"]

    assert [r["id"] for r in rows] == ["quiet-contested", "loud-contested", "loud-agreed"]


def test_unanimity_needs_more_than_one_voter() -> None:
    """A single opinion is not agreement, and calling it "confirm (1 agree)" would
    invite exactly the rubber-stamp this queue exists to avoid."""
    dataset = [{"id": "lonely", "seen": 5}]
    rows = build(dataset, {"a": _opinions("a", lonely="high")}, {})["rows"]

    assert rows[0]["unanimous"] is False
    assert rows[0]["spread"] == 0


def test_the_investigator_is_reported_and_never_voted() -> None:
    """The property the whole design rests on.

    The investigator had tool access and minutes; the judges had one cheap call
    each. Folding it into a majority would discard exactly the asymmetry that
    makes it evidence — so it must not move `spread` or `unanimous`, however
    strongly it disagrees.

    Measured on the real corpus, this is not hypothetical: the two largest rules
    (76% of volume) are unanimous `high` across all three cheap judges while the
    investigator's reports on them say mostly medium and low.
    """
    dataset = [{"id": "big", "seen": 400}]
    votes = {"a": _opinions("a", big="high"), "b": _opinions("b", big="high")}
    evidence = {"rules": {"big": {"tier2_investigator": {"verdicts": {"low": 9, "medium": 12, "high": 5}}}}}

    row = build(dataset, votes, evidence)["rows"][0]

    assert row["unanimous"] is True, "the cheap judges do agree"
    assert row["spread"] == 0, "and the investigator must not change that"
    assert row["investigator"] == {"low": 9, "medium": 12, "high": 5}, "but it is reported, loudly"


def test_the_join_bridges_a_slug_and_its_original() -> None:
    """An exact match found 2 of 32 rules, and one miss was the third-largest in
    the corpus with 25 investigator reports going unread."""
    assert _key("DatasourceNoData") == _key("datasource-no-data")
    assert _key("[MQ] Ready backlog growing") == _key("mq-ready-backlog-growing")
    assert _key("示例充值超限告警") == _key("示例充值超限告警"), "CJK names carry through unchanged"
    assert _key("alpha") != _key("beta"), "normalising must not merge different rules"

    dataset = [{"id": "datasource-no-data", "seen": 88}]
    evidence = {"rules": {"DatasourceNoData": {"tier1_outcome": {"alerts": 149}, "capped_at": "low"}}}

    row = build(dataset, {"a": _opinions("a", **{"datasource-no-data": "low"})}, evidence)["rows"][0]

    assert row["outcome"] == {"alerts": 149}
    assert row["capped_at"] == "low"


def test_the_contested_share_is_of_volume_not_of_rules() -> None:
    """ "5 of 32 contested" and "12% of traffic contested" are different claims,
    and only the second one says whether the afternoon is worth it."""
    dataset = [{"id": "big", "seen": 900}, {"id": "small", "seen": 100}]
    a = _opinions("a", big="high", small="low")
    b = _opinions("b", big="high", small="critical")

    summary = build(dataset, {"a": a, "b": b}, {})["summary"]

    assert summary["contested"] == 1
    assert summary["contested_volume_share"] == 0.1
