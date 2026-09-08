"""A condition that ended verifies the work it was about, and costs nothing.

Three things are asserted together because they are one change: a recovery is
not an investigation request (it used to buy a re-fire turn), it is recorded on
the investigation of the same condition, and the board reads it as the weakest
of the three verifications — the only one that needs nobody.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi.testclient import TestClient

from hookprobe import work
from hookprobe.app import create_app
from hookprobe.runs import RunStore
from hookprobe.service import RunService
from tests.helpers import FakeEngine, make_settings

TOKEN = "secret-token"


def _client(tmp_path, **overrides):
    settings = make_settings(tmp_path, token=TOKEN, escalate_levels=frozenset({"high", "critical"}), **overrides)
    engine = FakeEngine()
    service = RunService(settings, engine, RunStore(tmp_path / "results"))
    return TestClient(create_app(settings, service)), service, engine


def _wait(client: TestClient, key: str) -> dict[str, Any]:
    end = time.time() + 3
    while time.time() < end:
        r = client.get(f"/v1/runs/{key}", headers={"Authorization": f"Bearer {TOKEN}"})
        if r.status_code == 200 and r.json().get("status") in ("completed", "failed"):
            return r.json()
        time.sleep(0.02)
    raise AssertionError("run did not finish")


def _fire(client: TestClient, *, event_id: int = 7, title: str = "Payment gateway 5xx rate 8.1%") -> str:
    r = client.post(
        "/hooks/event",
        json={
            "source": "judge-notify",
            "title": title,
            "body": "…",
            "level": "high",
            "event_id": event_id,
            "fields": {},
        },
    )
    assert r.json()["status"] == "accepted", r.text
    key = r.json()["sessionKey"]
    _wait(client, key)
    return key


def _recover(
    client: TestClient,
    *,
    event_id: int = 8,
    title: str = "Payment gateway 5xx rate 8.1%",
    level: str = "low",
    flag: Any = True,
) -> dict:
    r = client.post(
        "/hooks/event",
        json={
            "source": "judge-notify",
            "title": title,
            "body": "back under 0.2%",
            "level": level,
            "event_id": event_id,
            "is_recovery": flag,
            "fields": {},
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_a_recovery_verifies_the_investigation_and_buys_no_turn(tmp_path) -> None:
    client, service, engine = _client(tmp_path)
    key = _fire(client)
    calls = engine.calls

    answer = _recover(client)

    assert answer == {"status": "verified", "by": "recovery", "sessionKey": key}
    assert engine.calls == calls, "a condition that ended is a fact, not a question"
    run = service.get(key)
    assert run.meta["recovered_at"] > 0 and run.meta["recovered_by_event"] == 8

    (item,) = work.resolve([run])
    assert item.verified and item.verified_by == "recovery" and item.state == work.DONE
    assert work.counts([item])["verified"] == 1


def test_it_is_recorded_below_the_escalation_bar_where_recoveries_actually_arrive(tmp_path) -> None:
    """A recovery inherits its firing's importance, and on the production
    deployment every one of them landed below the bar. Checked after the level
    gate, the fact would never have been recorded at all."""
    client, service, _ = _client(tmp_path)
    key = _fire(client)
    assert _recover(client, level="info")["status"] == "verified"
    assert service.get(key).meta["recovered_at"] > 0


def test_a_ruling_and_a_clean_procedure_both_outrank_it(tmp_path) -> None:
    client, service, _ = _client(tmp_path)
    key = _fire(client)
    _recover(client)
    run = service.get(key)

    run.ruling = "useful"
    (item,) = work.resolve([run])
    assert item.verified_by == "ruling", "a person's verdict is the strongest of the three"

    run.ruling = ""
    applied = {
        "id": "a" * 10,
        "session_key": key,
        "status": "applied",
        "steps": [{"command": "x"}],
        "results": [{"exit": 0}],
    }
    (item,) = work.resolve([run], proposals=[applied])
    assert item.verified_by == "remediation", "its own procedure running clean beats the condition merely ending"


def test_a_redelivered_recovery_changes_nothing_and_still_answers(tmp_path) -> None:
    client, service, _ = _client(tmp_path)
    key = _fire(client)
    first = _recover(client)
    at = service.get(key).meta["recovered_at"]
    time.sleep(0.01)
    assert _recover(client, event_id=9) == first, "the pipe retries; a condition ends once"
    assert service.get(key).meta["recovered_at"] == at
    assert service.get(key).meta["recovered_by_event"] == 8


def test_a_recovery_for_something_nobody_investigated_is_a_named_skip(tmp_path) -> None:
    client, _, engine = _client(tmp_path)
    answer = _recover(client, title="a condition this node never saw")
    assert answer["status"] == "skipped" and "no investigation" in answer["reason"]
    assert engine.calls == 0


def test_only_a_stated_recovery_counts_never_a_guess_at_the_words(tmp_path) -> None:
    """The judge decides what a recovery is; this door reads the answer. A title
    that merely looks resolved must still be investigated — inferring it here
    would be a content judgement in the one service that refuses to make them."""
    client, service, _ = _client(tmp_path)
    r = client.post(
        "/hooks/event",
        json={
            "source": "judge-notify",
            "title": "[RESOLVED] disk filled up again",
            "body": "…",
            "level": "high",
            "event_id": 30,
            "fields": {},
        },
    )
    assert r.json()["status"] == "accepted", "no is_recovery flag: an ordinary investigation"
    _wait(client, r.json()["sessionKey"])

    # And the string forms the pipe may send are all understood.
    key = _fire(client, event_id=40, title="Redis slow command rate high")
    assert _recover(client, event_id=41, title="Redis slow command rate high", flag="resolved")["status"] == "verified"
    assert service.get(key).meta["recovered_at"] > 0
