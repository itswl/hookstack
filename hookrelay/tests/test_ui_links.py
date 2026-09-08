"""A chain's hop can point at the console that owns it — the pipe only hands over the address.

The pipe stays content-blind: `session` and `sender` are two field NAMES the
doors agreed on, copied as identifiers, never read. Where they lead is the
deployment's business (HOOKRELAY_UI_LINKS), and an empty setting means no links.
"""

from __future__ import annotations

import os
from unittest import mock

from hookrelay.settings import Settings
from hookrelay.timeline import render


def test_ui_links_parse_as_door_to_url_pairs_and_default_to_none() -> None:
    raw = " probe-notify = http://127.0.0.1:8088/ui/ ,plan-notify=http://127.0.0.1:8089/ui,, broken-pair "
    with mock.patch.dict(os.environ, {"HOOKRELAY_UI_LINKS": raw}, clear=False):
        assert Settings.load().ui_links == {
            "probe-notify": "http://127.0.0.1:8088/ui",
            "plan-notify": "http://127.0.0.1:8089/ui",
        }
    with mock.patch.dict(os.environ, {"HOOKRELAY_UI_LINKS": ""}, clear=False):
        assert Settings.load().ui_links == {}


def _hop(id_: int, at: float, source: str, *, correlation: str = "", fields: dict | None = None) -> dict:
    return {
        "id": id_,
        "received_at": at,
        "source": source,
        "title": "disk",
        "level": "high",
        "outcome": "routed",
        "channels": ["somewhere"],
        "correlation_id": correlation,
        "fields": fields or {},
    }


def test_a_hop_carries_the_session_it_names_and_who_spoke_from_chat() -> None:
    rows = [
        _hop(1, 100.0, "alertmanager"),
        _hop(2, 130.0, "probe-notify", correlation="hr-1", fields={"session": "probe:judge-notify:1", "cost_usd": 0.4}),
        _hop(
            3, 200.0, "lark-thread", correlation="hr-1", fields={"session": "probe:judge-notify:1", "sender": "ou_sre"}
        ),
    ]
    (chain,) = render(rows)["chains"]
    by_door = {h["door"]: h for h in chain["hops"]}
    assert by_door["alertmanager"]["session"] is None and by_door["alertmanager"]["sender"] is None
    assert by_door["probe-notify"]["session"] == "probe:judge-notify:1"
    assert by_door["lark-thread"]["sender"] == "ou_sre"
