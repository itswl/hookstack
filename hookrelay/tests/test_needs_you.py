"""scripts/needs_you.py — the board's figures as a morning card.

The same rules the board draws from, over the same two feeds: a card that asked
and no press in its chain is waiting; a stated recovery or a held fix is ended;
a routed single hop under two hours old is in flight; ticks, repeats and
recoveries are quiet. The card's title is the one line a person decides from.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "needs_you", Path(__file__).resolve().parents[2] / "scripts" / "needs_you.py"
)
needs_you = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(needs_you)

NOW = 1_800_000_000.0


def _hop(id_: int, at: float, door: str, title: str = "disk 94%", **extra):
    return {"id": id_, "at": at, "door": door, "title": title, "outcome": "routed", "to": ["x"], **extra}


def _row(id_: int, source: str, title: str = "disk 94%", **extra):
    return {"id": id_, "source": source, "title": title, "received_at": 0, "fields": {}, "deliveries": [], **extra}


TIMELINE = {
    "chains": [
        # asked, nobody pressed → waiting
        {
            "chain": "1",
            "started_at": NOW - 7200,
            "hops": [
                _hop(1, NOW - 7200, "inbound"),
                _hop(2, NOW - 7100, "probe-notify", "disk 94% · investigation", asked=["Approve: df -h", "Ask why"]),
            ],
        },
        # asked and pressed → not waiting
        {
            "chain": "3",
            "started_at": NOW - 9000,
            "hops": [
                _hop(3, NOW - 9000, "inbound", "cpu"),
                _hop(4, NOW - 8900, "judge-notify", "cpu", asked=["Worth waking me"]),
                _hop(5, NOW - 8000, "card-action", "card action: useful"),
            ],
        },
        # a fix that held → ended well
        {
            "chain": "6",
            "started_at": NOW - 3000,
            "hops": [_hop(6, NOW - 3000, "inbound", "mem"), _hop(7, NOW - 2900, "probe-notify", "mem · fix held")],
        },
        # routed, nothing back, fresh → in flight
        {"chain": "8", "started_at": NOW - 600, "hops": [_hop(8, NOW - 600, "inbound", "io")]},
        # a tick → quiet
        {"chain": "9", "started_at": NOW - 100, "hops": [_hop(9, NOW - 100, "watch-due", "round")]},
        # a recovery chain → quiet (its firing, chain 10, is ended)
        {"chain": "10", "started_at": NOW - 5000, "hops": [_hop(10, NOW - 5000, "inbound", "net")]},
        {"chain": "11", "started_at": NOW - 4000, "hops": [_hop(11, NOW - 4000, "inbound", "net")]},
    ]
}
STATUS = {
    "queue": {"queued": 0, "sent": 40, "dead": 2},
    "recent": [
        _row(1, "inbound"),
        _row(
            2,
            "probe-notify",
            "disk 94% · investigation",
            deliveries=[{"channel": "ops-feishu", "status": "sent", "asked": ["Approve: df -h", "Ask why"]}],
        ),
        _row(3, "inbound", "cpu"),
        _row(
            4,
            "judge-notify",
            "cpu",
            deliveries=[{"channel": "ops-feishu", "status": "sent", "asked": ["Worth waking me"]}],
        ),
        _row(5, "card-action", "card action: useful", fields={"kind": "useful", "actor": "ou_x"}),
        _row(6, "inbound", "mem"),
        _row(7, "probe-notify", "mem · fix held"),
        _row(8, "inbound", "io"),
        _row(9, "watch-due", "round", fields={"kind": "brief"}),
        _row(10, "inbound", "net", received_at=NOW - 5000),
        _row(11, "inbound", "net", received_at=NOW - 4000, is_recovery=1),
    ],
}


def test_the_figures_follow_the_boards_rules():
    figures = needs_you.analyse(TIMELINE, STATUS, now=NOW)
    assert [w["chain"] for w in figures["waiting"]] == ["1"]
    assert figures["waiting"][0]["asked"] == ["Approve: df -h", "Ask why"] and figures["waiting"][0]["on"] == [
        "ops-feishu"
    ]
    assert figures["in_flight"] == 1, "io: routed, nothing back, ten minutes old"
    assert figures["ended_well"] == 2, "the held fix and the net recovery"
    assert figures["dead"] == 2
    assert figures["quiet"] == 2, "the tick and the recovery event itself"
    assert figures["chains"] == 7


def test_the_card_leads_with_what_waits_and_says_so_in_the_title():
    figures = needs_you.analyse(TIMELINE, STATUS, now=NOW)
    card = needs_you.signal(figures, now=NOW, board="http://127.0.0.1:8100/")
    assert card["title"].startswith("Needs you · 1 waiting · ")
    assert card["level"] == "low" and card["kind"] == "report" and card["origin"] == "needs-you"
    lines = card["detail"].splitlines()
    assert lines[0].startswith("· disk 94% — asked 1h ago on ops-feishu: Approve: df -h · Ask why  (#1)")
    assert "in flight 1 · dead letters 2 · ended well in 24h: 2 · quiet 2 of 7 chains" in lines[1]
    assert lines[2] == "board: http://127.0.0.1:8100/"

    quiet = needs_you.signal(needs_you.analyse({"chains": []}, {"queue": {}}, now=NOW), now=NOW)
    assert quiet["title"].startswith("Nothing waiting on you · ")
    assert "dead letters 0" in quiet["detail"]


def test_an_approved_fix_is_in_flight_and_an_ending_counts_from_when_it_ended():
    """Two rules the board draws and the card once missed. A procedure somebody
    approved is the machine's to finish, so it is in flight until a report or its
    outcome comes back — before, the card counted it nowhere. And an alert that
    fired yesterday and recovered an hour ago ended well TODAY: the recovery is a
    separate event, not a hop, so the chain's own hops alone dated it yesterday."""
    timeline = {
        "chains": [
            {
                "chain": "20",
                "started_at": NOW - 9000,
                "hops": [
                    _hop(20, NOW - 9000, "inbound", "queue"),
                    _hop(21, NOW - 8900, "probe-notify", "queue · investigation", session="probe:inbound:20"),
                    _hop(22, NOW - 8000, "card-action", "card action: approve"),
                ],
            },
            {"chain": "23", "started_at": NOW - 90000, "hops": [_hop(23, NOW - 90000, "inbound", "tls")]},
            {"chain": "24", "started_at": NOW - 4000, "hops": [_hop(24, NOW - 4000, "inbound", "tls")]},
        ]
    }
    status = {
        "queue": {},
        "recent": [
            _row(20, "inbound", "queue"),
            _row(21, "probe-notify", "queue · investigation"),
            _row(22, "card-action", "card action: approve", fields={"kind": "approve", "actor": "ou_x"}),
            _row(23, "inbound", "tls", received_at=NOW - 90000),
            _row(24, "inbound", "tls", received_at=NOW - 4000, is_recovery=1),
        ],
    }
    figures = needs_you.analyse(timeline, status, now=NOW)
    assert figures["waiting"] == [] and figures["in_flight"] == 1, "the approved procedure, nothing back since"
    assert figures["ended_well"] == 1, "fired 25 hours ago, recovered an hour ago"

    reported = {**timeline, "chains": [dict(timeline["chains"][0])]}
    reported["chains"][0]["hops"] = [
        *timeline["chains"][0]["hops"],
        _hop(25, NOW - 7000, "probe-notify", "queue · investigation", session="probe:inbound:20"),
    ]
    assert needs_you.analyse(reported, status, now=NOW)["in_flight"] == 0, "a report after the approval answered it"
