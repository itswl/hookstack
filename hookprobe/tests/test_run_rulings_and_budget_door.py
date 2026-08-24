"""The worth side of the ledger, and who the budget breaker may refuse.

Two gaps from the same cause: a decision that was right when it was made and
stopped being right when something upstream changed.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from hookprobe.app import create_app
from hookprobe.runs import RunStore
from hookprobe.service import RunService
from tests.helpers import FakeEngine, make_settings

TOKEN = "secret-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _client(tmp_path, **overrides):
    workdir = tmp_path / "wd"
    settings = make_settings(workdir, token=TOKEN, **overrides)
    service = RunService(settings, FakeEngine(), RunStore(workdir / "results"))
    return TestClient(create_app(settings, service))


def _start(client, message="look at mq-01"):
    return client.post("/hooks/agent", json={"message": message}, headers=AUTH).json()["sessionKey"]


def test_a_ruling_can_actually_be_written(tmp_path) -> None:
    """`ruled_useful` read 0 on every deployment and looked like apathy. The
    field, the window arithmetic and the /v1/budget line had existed since the
    ledger did; nothing could SET it, so the number was unreachable rather than
    ignored."""
    with _client(tmp_path) as client:
        key = _start(client)

        filed = client.post("/v1/runs/rulings", json={"useless": [key], "by": "ops-1"}, headers=AUTH)

        assert filed.status_code == 200
        assert filed.json()["filed"] == [key]
        detail = client.get(f"/v1/runs/{key}", headers=AUTH).json()
        assert detail["ruling"] == "useless"
        assert detail["ruled_by"] == "ops-1"
        assert detail["ruled_at"] is not None


def test_rulings_are_filed_in_bulk(tmp_path) -> None:
    """The friction was never the opinion, it was expressing it once per run.
    This service's own notes record how the per-item path went: "nobody presses
    the buttons on the cards"."""
    with _client(tmp_path) as client:
        keys = [_start(client, f"node-{i}") for i in range(3)]

        filed = client.post(
            "/v1/runs/rulings",
            json={"useless": keys[:2], "useful": keys[2:], "by": "ops-1"},
            headers=AUTH,
        )

        assert sorted(filed.json()["filed"]) == sorted(keys)
        verdicts = [client.get(f"/v1/runs/{k}", headers=AUTH).json()["ruling"] for k in keys]
        assert verdicts.count("useless") == 2
        assert verdicts.count("useful") == 1


def test_an_unknown_key_is_reported_not_swallowed(tmp_path) -> None:
    """A typo\'d key must not read as filed — the count is the whole point."""
    with _client(tmp_path) as client:
        filed = client.post("/v1/runs/rulings", json={"useless": ["no-such-session"]}, headers=AUTH)

        assert filed.json()["filed"] == []
        assert filed.json()["unknown"] == ["no-such-session"]


def test_a_meaningless_verdict_changes_nothing(tmp_path) -> None:
    with _client(tmp_path) as client:
        key = _start(client)

        client.post("/v1/runs/rulings", json={"maybe": [key]}, headers=AUTH)

        assert client.get(f"/v1/runs/{key}", headers=AUTH).json()["ruling"] == ""


def test_unruled_lists_only_what_still_owes_a_verdict(tmp_path) -> None:
    with _client(tmp_path) as client:
        keys = [_start(client, f"m{i}") for i in range(2)]
        client.post("/v1/runs/rulings", json={"useful": [keys[0]]}, headers=AUTH)

        unruled = {r["session_key"] for r in client.get("/v1/runs?unruled=1", headers=AUTH).json()}

        assert keys[0] not in unruled
        assert keys[1] in unruled


def test_the_budget_refuses_a_rule_but_never_a_person(tmp_path) -> None:
    """The breaker guards spending nobody asked for, and the DOOR used to be the
    proxy: /hooks/agent was operator-driven so it was never gated. A platform
    upstream now forwards matching alerts here automatically, so the door carries
    both kinds — and refusing the whole door would refuse the person, which is
    the failure the original design existed to avoid.

    Silence is treated as automated on purpose: a refused person can retry with
    the header, and an overspent budget cannot be un-spent. A refusal settles as
    a report-shaped run rather than an HTTP error, so the operator learns why
    instead of the answer disappearing into a caller's retry.
    """
    with _client(
        tmp_path,
        budget_usd=0.0000001,
        budget_window_hours=24.0,
        budget_gates_agent_door=True,
    ) as client:
        assert client.post("/hooks/agent", json={"message": "seed the ledger"}, headers=AUTH).status_code == 200

        undeclared = client.post("/hooks/agent", json={"message": "a rule asked"}, headers=AUTH)
        by_header = client.post(
            "/hooks/agent", json={"message": "a person asked"}, headers={**AUTH, "X-Operator": "true"}
        )
        by_body = client.post("/hooks/agent", json={"message": "a person asked", "operator": True}, headers=AUTH)

        assert undeclared.json().get("status") == "refused", "silence must be treated as automated, not as a person"
        assert by_header.json().get("status") != "refused", "a person who says so must be answered"
        assert by_body.json().get("status") != "refused", "the body field must work for a header-less client"


def test_the_meter_stays_off_until_it_is_armed(tmp_path) -> None:
    """HOOKPROBE_BUDGET_GATES_AGENT_DOOR is opt-in: a deployment that has not
    armed it must keep answering automated triggers, or upgrading the service
    would silently start dropping investigations."""
    with _client(tmp_path, budget_usd=0.0000001, budget_window_hours=24.0) as client:
        client.post("/hooks/agent", json={"message": "seed"}, headers=AUTH)

        automated = client.post("/hooks/agent", json={"message": "a rule asked"}, headers=AUTH)

        assert automated.json().get("status") != "refused"
