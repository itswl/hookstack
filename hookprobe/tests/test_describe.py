"""The Agent Description document: what it says, and what it must never say.

The shape was validated once against ANP's own parser
(`anp.anp_crawler.anp_parser.ANPDocumentParser`), which read all five
interfaces — recorded in the commit rather than asserted here, because adding
that SDK as a test dependency is the coupling the evaluation note refused.
What this file pins is everything that can be checked without it.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from hookprobe.app import create_app
from hookprobe.describe import agent_description
from hookprobe.runs import RunStore
from hookprobe.service import RunService
from tests.helpers import FakeEngine, make_settings

AUTH = {"Authorization": "Bearer secret-token"}


def test_the_document_withholds_what_v1_agent_answers(tmp_path: Path) -> None:
    """`/v1/agent` says model, gateway endpoint, workspace and budget. A
    crawlable document says none of them: that is "how is this configured",
    which is the operator's business — and the endpoint host is an estate
    identifier, which is the one category this repository never publishes.
    """
    settings = make_settings(
        tmp_path,
        agent_name="planner",
        agent_role="turns a work signal into a plan",
        model="gpt-5.6-luna",
        budget_usd=25.0,
    )
    blob = json.dumps(agent_description(settings, version="0.1.0", now=0))

    for forbidden in ("gpt-5.6-luna", str(tmp_path), "25.0", "budget", "workspace", "model"):
        assert forbidden not in blob, f"the description leaks {forbidden!r}"
    # And it still says the two things a caller needs.
    assert "planner" in blob and "turns a work signal into a plan" in blob


def test_an_unreachable_node_does_not_invent_an_origin(tmp_path: Path) -> None:
    """Same rule the budget ceiling learned: do not print what was not measured.
    Every deployment here is loopback-bound, so the default is paths plus an
    explicit `reachable: false` rather than an address that would resolve to
    the caller's own machine."""
    doc = agent_description(make_settings(tmp_path), version="0.1.0", now=0)
    assert doc["reachable"] is False
    assert "url" not in doc, "no self-URL when the node has no address to state"
    assert [i["url"] for i in doc["interfaces"]][0].startswith("/")

    stated = agent_description(make_settings(tmp_path, public_url="https://planner.example.org/"), version="0.1.0")
    assert stated["reachable"] is True
    assert stated["url"] == "https://planner.example.org/v1/agent/description", "trailing slash normalised"
    assert all(i["url"].startswith("https://planner.example.org/") for i in stated["interfaces"])


def test_the_door_that_needs_a_person_says_so_in_the_standard_field(tmp_path: Path) -> None:
    """The reason this is emitted in somebody else's dialect rather than a
    private one: ANP carries `humanAuthorization` as a first-class boolean, so a
    caller can see which doors will not move without a person BEFORE calling."""
    doc = agent_description(make_settings(tmp_path), version="0.1.0", now=0)
    gated = [i for i in doc["interfaces"] if i.get("humanAuthorization")]
    assert [i["url"] for i in gated] == ["/v1/remediations/{proposal_id}/approve"]
    # Not set to False elsewhere — absent means "not claimed", which is the
    # honest default for a field a reader may not understand.
    assert all("humanAuthorization" not in i for i in doc["interfaces"] if i not in gated)


def test_the_route_is_guarded_like_every_other(tmp_path: Path) -> None:
    """An Agent Description is meant to be crawlable, and on a node with a
    reachable address that is the point. These nodes have none, so publishing
    it unauthenticated would widen the surface and buy nothing."""
    settings = make_settings(tmp_path, agent_name="planner")
    service = RunService(settings, FakeEngine(), RunStore(tmp_path / "results"))
    with TestClient(create_app(settings, service)) as client:
        assert client.get("/v1/agent/description").status_code == 401
        body = client.get("/v1/agent/description", headers=AUTH).json()
        assert body["protocolType"] == "ANP" and body["type"] == "AgentDescription"
        assert body["name"] == "planner"
        assert len(body["interfaces"]) == 5
