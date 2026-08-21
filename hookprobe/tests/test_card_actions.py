"""The card is not a dead end: what a report offers, and what a press does.

Two things carry the weight here. The door spends money — `followup` starts a
paid turn and `approve` runs commands at a live target — so a redelivery of one
press must do neither twice. And a press stands in for the operator's console
click and for nothing else: the allowlist gate has to refuse a card exactly as
it refuses the console.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hookprobe import actions, remediation
from hookprobe.app import create_app
from hookprobe.engine import EngineResult
from hookprobe.notify import ReturnDelivery
from hookprobe.runs import COMPLETED, Run, RunStore
from hookprobe.service import RunService
from hookprobe.wire import sign_timestamped
from tests.helpers import FakeEngine, GatedEngine, make_settings

TOKEN = "secret-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

EVENT = {
    "title": "Payment gateway 5xx rate 8.1%",
    "body": "gateway-2 5xx at 8.1% over the last 5 minutes",
    "level": "high",
    "source": "inbound",
    "event_id": 5,
    "fields": {"env": "prod"},
}

REPORT = """gateway-2 is returning 5xx because its connection pool is exhausted.

```remediation
[{"action": "restart the gateway", "command": "kubectl rollout restart deploy/gateway-2 -n prod",
  "target": "gateway-2", "risk": "medium", "rollback": "kubectl rollout undo deploy/gateway-2 -n prod"}]
```
"""


def _client(tmp_path: Path, engine: Any, **overrides: Any) -> TestClient:
    settings = make_settings(tmp_path, token=TOKEN, **overrides)
    service = RunService(settings, engine, RunStore(tmp_path / "results"))
    return TestClient(create_app(settings, service))


def _drain(client: TestClient, key: str) -> dict[str, Any]:
    for _ in range(400):
        detail = client.get(f"/v1/runs/{key}", headers=AUTH).json()
        if detail["status"] != "running":
            return detail
        time.sleep(0.01)
    raise AssertionError("run never finished")


def _press(
    client: TestClient,
    kind: str,
    *,
    params: dict[str, Any] | None = None,
    correlation_id: str = "hr-1",
    event_id: Any = 5,
    actor: str = "ou_operator",
    at: Any = 1786037727,
    secret: str = "",
    sign: bool = True,
) -> Any:
    """One button press, delivered the way hookrelay delivers it."""
    body = json.dumps(
        {
            "action": {"kind": kind, "params": params or {}},
            "correlation_id": correlation_id,
            "event_id": event_id,
            "actor": actor,
            "at": at,
        }
    ).encode()
    headers = {"Content-Type": "application/json"}
    if sign:
        headers.update(sign_timestamped(secret, body))
    return client.post("/hooks/action", content=body, headers=headers)


def _investigated(tmp_path: Path, text: str = REPORT, **overrides: Any) -> tuple[TestClient, FakeEngine]:
    """A finished relay-born investigation, reached through the real event door."""
    engine = FakeEngine(result=EngineResult(text=text, message_count=2, cost_usd=0.5, session_id="sdk-1"))
    client = _client(tmp_path, engine, **overrides)
    client.__enter__()
    body = json.dumps(EVENT).encode()
    headers = {"Content-Type": "application/json", **sign_timestamped(str(overrides.get("event_secret") or ""), body)}
    assert client.post("/hooks/event", content=body, headers=headers).json()["status"] == "accepted"
    _drain(client, "probe:inbound:5")
    return client, engine


# -- what the report declares ------------------------------------------------


def test_a_report_with_a_procedure_offers_it_by_name(tmp_path: Path) -> None:
    """An approve button whose text does not say what will run is a trap — the
    card has one line where the console has the whole step list and a confirm
    dialog, so the COMMAND goes in the label, not the model's prose for it."""
    client, _ = _investigated(tmp_path)
    with client:
        run = _drain(client, "probe:inbound:5")
        proposal_id = run["meta"]["remediation_proposal"]
        declared = actions.declare(Run(**{k: v for k, v in run.items() if k != "inputs_now"}), tmp_path)

    kinds = [action["kind"] for action in declared]
    assert kinds == ["followup", "approve", "useful", "useless"]
    approve = declared[1]
    assert approve["ref"] == proposal_id
    assert "kubectl rollout restart deploy/gateway-2" in approve["text"]
    assert "medium risk" in approve["text"], "a person judging at a glance wants the risk"
    assert len(approve["text"]) <= 72, "a label nobody reads is not a label"
    # Every other key rides along as an opaque param; nothing names a channel
    # and nothing here is signed — that half is the pipe's.
    assert set(declared[0]) == {"kind", "text", "prompt"}
    assert declared[0]["prompt"].startswith("Why do you believe that?")


def test_a_failed_investigation_offers_no_approval(tmp_path: Path) -> None:
    """A run that died has nothing to approve, and its follow-up asks a different
    question — pressing "why do you believe that" at a crashed run gets an
    apology, not evidence."""
    engine = FakeEngine(result=EngineResult(text="", message_count=0, session_id="sdk-1", error="boom"))
    with _client(tmp_path, engine) as client:
        client.post("/hooks/event", json=EVENT)
        run = _drain(client, "probe:inbound:5")

    declared = actions.declare(Run(**{k: v for k, v in run.items() if k != "inputs_now"}), tmp_path)
    assert [action["kind"] for action in declared] == ["followup", "useful", "useless"]
    assert declared[0]["prompt"].startswith("This investigation did not finish")


def test_a_run_with_no_session_to_resume_offers_no_followup(tmp_path: Path) -> None:
    """A budget refusal never reached an engine, so there is nothing to continue
    — and the ruling pair still rides along, because "was this worth it" is a
    question a refusal deserves too."""
    run = Run(session_key="probe:inbound:9", run_id="r1", status=COMPLETED, error="refused: budget exhausted")
    assert [action["kind"] for action in actions.declare(run, tmp_path)] == ["useful", "useless"]


def test_a_proposal_already_settled_is_not_offered_again(tmp_path: Path) -> None:
    """The console and the card look at the same row. One that has been approved
    or rejected must not collect a second press."""
    run = Run(session_key="probe:inbound:5", run_id="r1", status=COMPLETED, engine_session_id="sdk-1")
    steps = [{"action": "restart", "command": "systemctl restart api", "risk": "low"}]
    proposal_id = remediation.propose(tmp_path, "probe:inbound:5", steps)
    assert [a["kind"] for a in actions.declare(run, tmp_path)] == ["followup", "approve", "useful", "useless"]

    remediation.reject(tmp_path, proposal_id)
    assert [a["kind"] for a in actions.declare(run, tmp_path)] == ["followup", "useful", "useless"]


class _Recorder(ReturnDelivery):
    """A return delivery that keeps the body instead of posting it."""

    def __init__(self, settings: Any, store: Any) -> None:
        super().__init__(settings, store)
        self.bodies: list[bytes] = []

    def _post_return(self, body: bytes) -> int:
        self.bodies.append(body)
        return 200


def test_the_returned_report_carries_its_actions(tmp_path: Path) -> None:
    """The processed-event dialect is what the pipe dresses, so a declaration
    that never reaches that payload is a declaration nobody can press."""

    async def scenario() -> dict[str, Any]:
        settings = make_settings(tmp_path, return_url="http://pipe.invalid/probe-notify")
        store = RunStore(tmp_path / "results")
        run = Run(
            session_key="probe:inbound:5",
            run_id="r1",
            status=COMPLETED,
            text="the pool is exhausted",
            engine_session_id="sdk-1",
            origin="relay",
        )
        run.meta = {"title": "Payment gateway 5xx", "level": "high", "source": "inbound", "event_id": 5}
        store.create(run)
        recorder = _Recorder(settings, store)
        await recorder.deliver(run, (0.0,))
        return json.loads(recorder.bodies[0])

    payload = asyncio.run(scenario())
    assert [action["kind"] for action in payload["actions"]] == ["followup", "useful", "useless"]
    assert payload["meta"]["event_id"] == 5, "the id the press comes home on"


# -- the door ----------------------------------------------------------------


def test_the_action_door_needs_the_pipes_signature(tmp_path: Path) -> None:
    client, _ = _investigated(tmp_path, event_secret="pipe-secret")
    with client:
        assert _press(client, "useful", sign=False).status_code == 401
        assert _press(client, "useful", secret="wrong-secret").status_code == 401
        assert _press(client, "useful", secret="pipe-secret").status_code == 202


def test_an_unknown_kind_is_refused_before_anything_is_claimed(tmp_path: Path) -> None:
    client, engine = _investigated(tmp_path)
    with client:
        response = _press(client, "delete-everything")
        assert response.status_code == 400
        assert "unknown action kind" in response.json()["detail"]
        assert engine.calls == 1
        assert not (tmp_path / actions.DIRNAME).exists(), "a kind we do not speak claims nothing"


def test_a_followup_resumes_the_session_the_card_came_from(tmp_path: Path) -> None:
    """The card carries the alert's event id, which is the one identifier both
    sides agree on — hookprobe never saw the pipe's correlation id."""
    client, engine = _investigated(tmp_path)
    with client:
        response = _press(client, "followup", params={"prompt": "Which pod exhausted the pool?"})
        assert response.status_code == 202
        answer = response.json()
        assert answer["status"] == "investigating"
        assert answer["sessionKey"] == "probe:inbound:5"
        _drain(client, "probe:inbound:5")

    assert engine.calls == 2
    assert "Which pod exhausted the pool?" in engine.messages[1]
    assert "pressed a button" in engine.messages[1], "the model is told who is asking and why briefly"
    assert engine.resumes[1] == "sdk-1", "the follow-up keeps everything the first pass gathered"


def test_a_followup_with_no_question_asks_the_default_one(tmp_path: Path) -> None:
    client, engine = _investigated(tmp_path)
    with client:
        assert _press(client, "followup").json()["status"] == "investigating"
        _drain(client, "probe:inbound:5")
    assert "Why do you believe that?" in engine.messages[1]


def test_a_press_on_a_card_whose_investigation_is_gone_costs_nothing_and_alarms_nobody(tmp_path: Path) -> None:
    """202 with a reason, not 404.

    A card in a chat outlives its run — retention prunes case files — so
    scrolling up and pressing a stale button is the expected steady state. The
    pipe reads a non-2xx as a delivery failure, so a 404 would retry with
    backoff, dead-letter and fire the self-alarm: the one alarm that must not
    cry wolf, for a miss that is permanent anyway.
    """
    client, engine = _investigated(tmp_path)
    with client:
        response = _press(client, "followup", event_id=999, correlation_id="hr-unknown")
        assert response.status_code == 202
        assert response.json()["status"] == "no_such_investigation"
        assert engine.calls == 1, "no turn was started, so nothing was paid for"


def test_a_correlation_id_that_names_a_session_is_honoured(tmp_path: Path) -> None:
    """The event id is the general mapping; a pipe that wants to be explicit can
    hand back the session key itself and be believed."""
    client, _ = _investigated(tmp_path)
    with client:
        answer = _press(client, "useful", correlation_id="probe:inbound:5", event_id=None).json()
        assert answer["sessionKey"] == "probe:inbound:5"


# -- idempotency: this door spends money -------------------------------------


def test_a_redelivered_press_does_not_buy_a_second_turn(tmp_path: Path) -> None:
    """An IM platform retries a callback it did not hear an answer to. The claim
    on (correlation_id, kind, at) is what stands between that retry and a second
    paid investigation; the retry reads back the first answer instead."""
    client, engine = _investigated(tmp_path)
    with client:
        first = _press(client, "followup", at=1786037727).json()
        _drain(client, "probe:inbound:5")
        again = _press(client, "followup", at=1786037727)

        assert again.status_code == 202
        assert again.json()["duplicate"] is True
        assert again.json()["runId"] == first["runId"], "the retry hears what the press did"
        assert engine.calls == 2, "one press, one turn"

        # A different `at` is a different press: somebody who presses twice an
        # hour apart means it twice, and only the timestamp tells that apart.
        assert _press(client, "followup", at=1786041327).json()["status"] == "investigating"
        _drain(client, "probe:inbound:5")
    assert engine.calls == 3


def test_a_second_press_while_a_turn_is_running_is_told_so(tmp_path: Path) -> None:
    engine = GatedEngine()
    with _client(tmp_path, engine) as client:
        client.post("/hooks/event", json=EVENT)
        engine.release.set()
        _drain(client, "probe:inbound:5")

        engine.release.clear()
        assert _press(client, "followup", at=1).json()["status"] == "investigating"
        answer = _press(client, "followup", at=2).json()
        assert answer["status"] == "busy"
        assert "already in flight" in answer["detail"]
        engine.release.set()
        _drain(client, "probe:inbound:5")


def test_a_press_that_named_nothing_gives_its_claim_back(tmp_path: Path) -> None:
    """A delivery that 404s did no work, so it must not hold the key: otherwise
    the retry that arrives after somebody fixes the target is answered "already
    in flight" by a claim with nothing behind it, and the press is lost."""
    allow = tmp_path / "allow.txt"
    allow.write_text("kubectl rollout restart .*\n", encoding="utf-8")
    client, _ = _investigated(tmp_path, remediation_allowlist=allow)
    with client:
        run = _drain(client, "probe:inbound:5")
        proposal_id = run["meta"]["remediation_proposal"]

        assert _press(client, "approve", params={"ref": "0000000000"}, at=7).status_code == 404
        # Same press identity, this time naming something real.
        retried = _press(client, "approve", params={"ref": proposal_id}, at=7).json()
        assert retried["status"] == "approved"


# -- the gates ---------------------------------------------------------------


def test_a_card_press_cannot_stand_in_for_the_allowlist(tmp_path: Path) -> None:
    """A press is the operator's click and nothing more. Without an allowlist the
    console refuses too, and the whole point of the second gate is that no click
    — from a chat window or anywhere else — can supply it."""
    client, _ = _investigated(tmp_path, remediation_allowlist=None)
    with client:
        run = _drain(client, "probe:inbound:5")
        proposal_id = run["meta"]["remediation_proposal"]
        answer = _press(client, "approve", params={"ref": proposal_id}).json()

        assert answer["status"] == "denied"
        assert "no allowlist configured" in answer["detail"]
    row = remediation.load(tmp_path, proposal_id)
    assert row["status"] == "proposed", "still waiting on a human, exactly as before the press"
    assert not row["results"], "nothing ran"


def test_a_press_only_runs_what_the_allowlist_already_permitted(tmp_path: Path) -> None:
    allow = tmp_path / "allow.txt"
    allow.write_text("echo .*\n", encoding="utf-8")
    report = 'ok\n```remediation\n[{"action":"probe","command":"echo repaired","risk":"low"}]\n```\n'
    client, _ = _investigated(tmp_path, text=report, remediation_allowlist=allow)
    with client:
        run = _drain(client, "probe:inbound:5")
        proposal_id = run["meta"]["remediation_proposal"]
        answer = _press(client, "approve", params={"ref": proposal_id}, actor="ou_night_shift").json()
        assert answer["status"] == "approved"
        for _ in range(400):
            row = remediation.load(tmp_path, proposal_id)
            if row["status"] in ("executed", "failed"):
                break
            time.sleep(0.01)

    assert row["status"] == "executed"
    assert "repaired" in row["results"][0]["output"]
    # The console click never had a WHO. A press does, so the row keeps it.
    assert "ou_night_shift" in row["approved_note"] and "hr-1" in row["approved_note"]


def test_a_redelivered_approval_does_not_run_the_commands_twice(tmp_path: Path) -> None:
    allow = tmp_path / "allow.txt"
    allow.write_text("echo .*\n", encoding="utf-8")
    report = 'ok\n```remediation\n[{"action":"probe","command":"echo once","risk":"low"}]\n```\n'
    client, _ = _investigated(tmp_path, text=report, remediation_allowlist=allow)
    with client:
        proposal_id = _drain(client, "probe:inbound:5")["meta"]["remediation_proposal"]
        assert _press(client, "approve", params={"ref": proposal_id}).json()["status"] == "approved"
        for _ in range(400):
            if remediation.load(tmp_path, proposal_id)["status"] in ("executed", "failed"):
                break
            time.sleep(0.01)
        again = _press(client, "approve", params={"ref": proposal_id}).json()
        assert again["duplicate"] is True and again["status"] == "approved"

    row = remediation.load(tmp_path, proposal_id)
    assert len(row["results"]) == 1, "the command ran once"
    audit = "".join(path.read_text(encoding="utf-8") for path in (tmp_path / "audit").glob("*.jsonl"))
    assert audit.count("echo once") == 1


def test_a_press_on_a_proposal_somebody_already_settled_is_stale_not_a_rerun(tmp_path: Path) -> None:
    """The card outlives the row. A proposal rejected from the console, or
    settled by the boot sweep, must not be revivable by an old button."""
    client, _ = _investigated(tmp_path)
    with client:
        proposal_id = _drain(client, "probe:inbound:5")["meta"]["remediation_proposal"]
        assert client.post(f"/v1/remediations/{proposal_id}/reject", headers=AUTH).status_code == 200
        answer = _press(client, "approve", params={"ref": proposal_id}).json()

    assert answer["status"] == "stale"
    assert "rejected" in answer["detail"]
    assert remediation.load(tmp_path, proposal_id)["status"] == "rejected"


def test_approve_without_a_ref_is_refused(tmp_path: Path) -> None:
    client, _ = _investigated(tmp_path)
    with client:
        response = _press(client, "approve", params={})
        assert response.status_code == 400
        assert "params.ref" in response.json()["detail"]


# -- was the investigation worth it? -----------------------------------------


def test_a_ruling_lands_on_the_run_and_on_the_budget_line(tmp_path: Path) -> None:
    """Cost was measured to the cent and worth was measured nowhere, so the
    question "you want me to pay a model per alert?" had a dollar figure and
    nothing to put beside it."""
    client, _ = _investigated(tmp_path)
    with client:
        before = client.get("/v1/budget", headers=AUTH).json()
        assert before["worth"] == "1 investigation, $0.50 — none ruled yet", "an absence, not a verdict"

        answer = _press(client, "useful", actor="ou_sre").json()
        assert answer == {
            "status": "recorded",
            "kind": "useful",
            "sessionKey": "probe:inbound:5",
            "ruling": "useful",
        }

        budget = client.get("/v1/budget", headers=AUTH).json()
        assert budget["worth"] == "1 investigation, $0.50, 1 of 1 ruled found the cause"
        assert (budget["investigations"], budget["ruled_useful"], budget["ruled_useless"]) == (1, 1, 0)
        # Visible per run too: an aggregate nobody can trace back is unreadable.
        assert client.get("/v1/runs", headers=AUTH).json()[0]["ruling"] == "useful"
        detail = client.get("/v1/runs/probe:inbound:5", headers=AUTH).json()
        assert detail["ruled_by"] == "ou_sre" and detail["ruled_at"] > 0


def test_a_miss_is_recorded_as_a_miss(tmp_path: Path) -> None:
    client, _ = _investigated(tmp_path)
    with client:
        assert _press(client, "useless").json()["ruling"] == "useless"
        budget = client.get("/v1/budget", headers=AUTH).json()

    # An unruled investigation is unrated, never a miss — so the two counts are
    # reported apart and the sentence only ever claims what somebody said.
    assert budget["worth"] == "1 investigation, $0.50, 0 of 1 ruled found the cause", (
        "ruled useless reads differently from unruled"
    )
    assert (budget["ruled_useful"], budget["ruled_useless"]) == (0, 1)


def test_a_ruling_does_not_restamp_the_run_it_judges(tmp_path: Path) -> None:
    """finish() moves finished_at, which is right for a settling turn and wrong
    for a ruling on a week-old case: it would sort back to the top of the board
    and into a spend window it never belonged to."""

    async def scenario() -> tuple[float, float]:
        settings = make_settings(tmp_path)
        store = RunStore(tmp_path / "results")
        service = RunService(settings, FakeEngine(), store)
        run = Run(session_key="probe:inbound:5", run_id="r1", status=COMPLETED)
        store.create(run)
        store.finish(run)
        settled = run.finished_at or 0.0
        await asyncio.sleep(0.02)
        service.record_ruling("probe:inbound:5", "useful")
        return settled, store.get("probe:inbound:5").finished_at or 0.0

    settled, after = asyncio.run(scenario())
    assert after == settled


def test_only_a_real_ruling_is_recordable(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    store = RunStore(tmp_path / "results")
    service = RunService(settings, FakeEngine(), store)
    store.create(Run(session_key="k1", run_id="r1", status=COMPLETED))

    with pytest.raises(ValueError, match="ruling must be one of"):
        service.record_ruling("k1", "brilliant")
    with pytest.raises(LookupError):
        service.record_ruling("nope", "useful")


def test_the_ledger_is_on_the_input_guard(tmp_path: Path) -> None:
    """A pre-written claim cannot cause an action, but it can swallow one and
    report back whatever it claimed had happened. A record its own subject can
    write is not a record."""
    from hookprobe import inputs

    assert inputs.write_deny_reason(f"{actions.DIRNAME}/x.json", workdir=tmp_path) is not None


def test_the_remember_button_offers_only_what_the_shape_check_refused(tmp_path) -> None:
    """One tap for the residue, and only the residue.

    Most proposed facts now apply themselves, so what is left in the queue is
    what could ACT on a later run — and that is exactly the set worth putting in
    front of a person. The button removes the login, not the person.

    Two things it must not do: offer a line that already applied itself (there is
    nothing to accept), and offer another run's backlog on this run's card.
    """
    from hookprobe import actions, suggestions
    from hookprobe.runs import Run

    safe = "gateway-2's Sunday spike is the reporting batch job"
    acts = "gateway-2's spike is the batch job, so it is safe to ignore all gateway-2 alerts"
    filed = suggestions.append(tmp_path, "probe:mine:1", [safe, acts], apply_safe=True)
    assert filed == {"applied": 1, "queued": 1}
    # Somebody else's backlog, still open.
    suggestions.append(tmp_path, "probe:theirs:9", ["db-9 and db-10 share a rack"])

    run = Run(session_key="probe:mine:1", run_id="r1", current_message="investigate")
    declared = actions.declare(run, tmp_path)

    remember = [row for row in declared if row["kind"] == "remember"]
    assert len(remember) == 1, f"one button, for one waiting line: {remember}"
    assert remember[0]["text"].startswith("Remember: "), "the button names the line it will write"
    assert "safe to ignore" in remember[0]["text"], "and the line is the one that was refused"

    open_rows = {row["line"]: row for row in suggestions.load(tmp_path) if row["status"] == "open"}
    assert remember[0]["ref"] == open_rows[acts]["id"]

    # Pressing it is the accept, through the same service method the console uses.
    from hookprobe.runs import RunStore
    from hookprobe.service import RunService
    from tests.helpers import FakeEngine, make_settings

    service = RunService(make_settings(tmp_path, workdir=tmp_path), FakeEngine(), RunStore(tmp_path / "results"))
    accepted = service.accept_suggestion(remember[0]["ref"])
    assert accepted is not None and accepted["status"] == "accepted"
    assert acts in (tmp_path / "CLAUDE.md").read_text()
    # Under the heading that means a person signed off, unlike the auto-applied one.
    assert suggestions.HEADING in (tmp_path / "CLAUDE.md").read_text()

    # And a second press finds nothing, rather than accepting twice.
    assert service.accept_suggestion(remember[0]["ref"]) is None
    assert not [row for row in actions.declare(run, tmp_path) if row["kind"] == "remember"]
