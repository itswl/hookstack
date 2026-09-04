"""The read that belongs before a route change.

The credential test is the one that matters most here: unlike `GET /config`,
which serves the file where secrets are still `${REFS}`, this renders the
RESOLVED config — so a Lark webhook URL arrives with its token already
substituted in.
"""

from __future__ import annotations

from hookrelay.config import Config
from hookrelay.topology import render


def _cfg(routes: list, channels: list | None = None, sources: list | None = None) -> Config:
    return Config.from_dict(
        {
            "sources": sources
            or [
                {"name": "watch", "secret": "s", "title": "{t}", "body": "{d}", "level": "{l}"},
                {"name": "plan-notify", "secret": "", "title": "{t}", "body": "{d}", "level": "{l}"},
            ],
            "channels": channels
            or [{"name": "to-plan", "type": "generic", "url": "http://probe-plan:8088/hooks/event"}],
            "routes": routes,
        }
    )


def test_a_chain_with_terminal_returns_warns_about_nothing() -> None:
    cfg = _cfg(
        [
            {"name": "watch-out", "source": "watch", "send_to": ["to-plan"], "priority": 100, "stop": True},
            {"name": "plan-out", "source": "plan-notify", "send_to": ["to-plan"], "priority": 90, "stop": True},
        ]
    )
    graph = render(cfg)
    assert graph["warnings"] == []
    assert all(door["walk_always_stops"] for door in graph["doors"])


def test_a_return_door_that_can_reach_a_wildcard_is_named() -> None:
    """The hazard four separate comments in this family's configs guard by hand:
    a wildcard route matches the return door too, so a node is handed its own
    output. The wildcard is legitimate on a front door, which is why this
    reports the shape and does not refuse the config."""
    cfg = _cfg(
        [
            {"name": "watch-out", "source": "watch", "send_to": ["to-plan"], "priority": 100, "stop": True},
            {"name": "everything", "source": "*", "send_to": ["to-plan"], "priority": 0},
        ]
    )
    warnings = render(cfg)["warnings"]

    fallthrough = [w for w in warnings if w["kind"] == "wildcard_fallthrough"]
    assert [w["door"] for w in fallthrough] == ["plan-notify"], (
        "only the door with no terminal route of its own falls through; "
        "watch-out stops the walk for watch before the wildcard is reached"
    )
    assert fallthrough[0]["route"] == "everything"


def test_a_stop_carrying_a_when_does_not_count_as_a_guarantee() -> None:
    """It stops the walk for the events it matches and no others — exactly the
    case where a fallthrough hides until the day an event misses the condition."""
    cfg = _cfg(
        [
            {
                "name": "watch-high",
                "source": "watch",
                "when": {"level": ["high"]},
                "send_to": ["to-plan"],
                "priority": 100,
                "stop": True,
            },
            {"name": "everything", "source": "*", "send_to": ["to-plan"], "priority": 0},
        ]
    )
    graph = render(cfg)
    watch = next(door for door in graph["doors"] if door["name"] == "watch")
    assert watch["walk_always_stops"] is False
    assert any(w["kind"] == "wildcard_fallthrough" and w["door"] == "watch" for w in graph["warnings"])


def test_an_exit_no_route_feeds_is_named() -> None:
    """The pipe's version of the alert stack's "no starved brain": a node that
    is configured, credentialed, and reachable by nothing."""
    cfg = _cfg(
        [{"name": "watch-out", "source": "watch", "send_to": ["to-plan"], "priority": 100, "stop": True}],
        channels=[
            {"name": "to-plan", "type": "generic", "url": "http://probe-plan:8088/hooks/event"},
            {"name": "to-nowhere", "type": "feishu", "url": "https://open.example/hook/abc"},
        ],
        sources=[{"name": "watch", "secret": "s", "title": "{t}", "body": "{d}", "level": "{l}"}],
    )
    starved = [w for w in render(cfg)["warnings"] if w["kind"] == "starved_exit"]
    assert [w["exit"] for w in starved] == ["to-nowhere"]


def test_a_channel_reached_only_by_a_card_button_is_not_starved() -> None:
    """Found by rendering a real deployment: `to-judge-feedback` and
    `to-probe-action` are fed by `card_actions.forward_to`, which does not walk
    the route table at all — counting routes alone called both dead. A warning
    that cries wolf on every deployment with buttons is a warning nobody reads.
    """
    cfg = Config.from_dict(
        {
            "sources": [{"name": "watch", "secret": "s", "title": "{t}", "body": "{d}", "level": "{l}"}],
            "channels": [
                {"name": "to-plan", "type": "generic", "url": "http://probe-plan:8088/hooks/event"},
                {"name": "to-feedback", "type": "generic", "url": "http://judge:8200/feedback"},
            ],
            "routes": [{"name": "out", "source": "watch", "send_to": ["to-plan"], "stop": True}],
            "card_actions": {"useful": {"forward_to": "to-feedback"}},
        }
    )
    graph = render(cfg)

    assert [w for w in graph["warnings"] if w["kind"] == "starved_exit"] == []
    feedback = next(exit_ for exit_ in graph["exits"] if exit_["name"] == "to-feedback")
    assert feedback["fed_by"] == [] and feedback["pressed_by"] == ["useful"], (
        "a button is a real edge and a distinct one — a person triggers it, not an event"
    )


def test_a_door_no_route_can_match_is_named() -> None:
    cfg = _cfg([{"name": "watch-out", "source": "watch", "send_to": ["to-plan"], "priority": 100, "stop": True}])
    unreachable = [w for w in render(cfg)["warnings"] if w["kind"] == "unreachable_door"]
    assert [w["door"] for w in unreachable] == ["plan-notify"]


def test_a_webhook_token_never_reaches_the_graph() -> None:
    """A Lark bot URL IS its credential and this renders the resolved config.
    Path, query and userinfo all carry secrets in the wild; only the host says
    which node an edge points at, and only the host is printed."""
    cfg = _cfg(
        [{"name": "out", "source": "watch", "send_to": ["to-lark"], "priority": 10, "stop": True}],
        channels=[
            {
                "name": "to-lark",
                "type": "feishu",
                "url": "https://open.example/open-apis/bot/v2/hook/SECRET-TOKEN?key=ALSO-SECRET",
            }
        ],
        sources=[{"name": "watch", "secret": "s", "title": "{t}", "body": "{d}", "level": "{l}"}],
    )
    graph = render(cfg)
    blob = repr(graph)

    assert "SECRET-TOKEN" not in blob and "ALSO-SECRET" not in blob
    assert graph["exits"][0]["target"] == "https://open.example"


def test_userinfo_is_dropped_with_the_rest_of_the_url() -> None:
    cfg = _cfg(
        [{"name": "out", "source": "watch", "send_to": ["to-node"], "priority": 10, "stop": True}],
        channels=[{"name": "to-node", "type": "generic", "url": "http://user:pw@node.internal:9000/hooks/event"}],
        sources=[{"name": "watch", "secret": "s", "title": "{t}", "body": "{d}", "level": "{l}"}],
    )
    graph = render(cfg)
    assert graph["exits"][0]["target"] == "http://node.internal:9000"
    assert "pw" not in repr(graph)


def test_the_graph_reports_where_an_inline_stage_is_placed() -> None:
    """`when` on an http stage is what makes a decider placeable, so a topology
    that did not show the placement would hide the orchestration."""
    cfg = Config.from_dict(
        {
            "sources": [{"name": "watch", "secret": "s", "title": "{t}", "body": "{d}", "level": "{l}"}],
            "channels": [{"name": "to-plan", "type": "generic", "url": "http://probe-plan:8088/hooks/event"}],
            "routes": [{"name": "out", "source": "watch", "send_to": ["to-plan"], "stop": True}],
            "pipeline": [
                {"type": "http", "name": "triage", "url": "http://triage:9000/d", "when": {"source": "watch"}},
                "routes",
            ],
        }
    )
    stage = next(s for s in render(cfg)["pipeline"] if s["name"] == "triage")
    assert stage["scoped_to"] == {"source": "watch"}


async def test_the_endpoint_is_admin_gated(client) -> None:
    assert (await client.get("/topology")).status_code == 403
    answer = await client.get("/topology", headers={"X-Admin-Token": "admin-t"})
    assert answer.status_code == 200
    assert {"doors", "pipeline", "exits", "warnings"} <= answer.json().keys()
