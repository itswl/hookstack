"""A person can rule on a CONDITION, and the ruling outranks the patrol's while it
is current.

Before this door the condition axis had one writer — the weekly ai-rulings
patrol — and `standing` was latest-wins over rows that carried no author. A
verdict written by hand would have been overwritten by the next Thursday's
inference, silently. Measured on production 2026-09-14: the two SES conditions
that took 70% of the week's investigator spend were ruled worth_it by the patrol,
and nobody had a way to say otherwise.
"""

from __future__ import annotations

import json
import time

from fastapi.testclient import TestClient

from hookprobe import rulings
from hookprobe.app import create_app
from hookprobe.distill import CASES_MARKER, slug
from hookprobe.runs import COMPLETED, Run, RunStore
from hookprobe.service import RunService
from tests.helpers import FakeEngine, make_settings

TOKEN = "secret-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
AGENT = {"Authorization": "Bearer agent-token"}
TITLE = "[SES] bounce volume high (24h)"


def _client(tmp_path):
    settings = make_settings(tmp_path, token=TOKEN)
    service = RunService(settings, FakeEngine(), RunStore(tmp_path / "results"))
    return TestClient(create_app(settings, service)), service, settings


def _rows(workdir):
    return [json.loads(line) for line in (workdir / "rulings.jsonl").read_text().splitlines() if line.strip()]


def test_a_person_files_a_condition_ruling_and_it_outranks_the_patrol(tmp_path):
    client, _, settings = _client(tmp_path)

    response = client.post(
        "/v1/rulings",
        json={"title": TITLE, "verdict": "not_worth_it", "why": "same finding six times a day", "by": "op-1"},
        headers=AUTH,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ruled_by"] == "operator:op-1" and body["standing"] is True
    assert body["runbook_present"] is False and "gates nothing" in body["consequence"]

    (row,) = _rows(settings.workdir)
    assert row["identity"] == f"operator|{TITLE}" and row["ruled_by"] == "operator:op-1"
    assert rulings.condition_of(row["identity"]) == TITLE

    # Thursday comes: the patrol refiles worth_it, later. Latest-wins alone
    # would have made that the standing verdict.
    rulings.record_local(
        settings.workdir, [{"identity": f"ww|{TITLE}|origin=grafana", "verdict": "worth_it", "why": "found"}], model="m"
    )
    standing = rulings.standing(settings.workdir, TITLE, ttl_days=14)
    assert standing is not None and standing["verdict"] == "not_worth_it"
    assert standing["ruled_by"] == "operator:op-1"


def test_an_expired_operator_ruling_lapses_to_the_current_row(tmp_path):
    """Precedence lasts exactly as long as the ruling is current. Past the TTL a
    person's verdict is as stale as anyone's, and the newest current row answers."""
    workdir = tmp_path
    rulings.file_operator_ruling(workdir, title=TITLE, verdict="not_worth_it", why="old decision", by="op-1")
    rows = _rows(workdir)
    rows[0]["at"] = time.time() - 20 * 86400
    (workdir / "rulings.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    rulings.record_local(
        workdir, [{"identity": f"ww|{TITLE}|origin=grafana", "verdict": "worth_it", "why": "x"}], model="m"
    )

    standing = rulings.standing(workdir, TITLE, ttl_days=14)
    assert standing is not None and standing["verdict"] == "worth_it"


def test_a_patrol_row_still_reads_as_it_always_did(tmp_path):
    """No author on an inferred row: the rows the patrol has been writing keep
    their shape, so nothing that reads them learns a new key by accident."""
    rulings.record_local(tmp_path, [{"identity": f"ww|{TITLE}", "verdict": "worth_it", "why": "x"}], model="m")
    (row,) = _rows(tmp_path)
    assert "ruled_by" not in row


def test_the_agent_bearer_cannot_rule_a_condition(tmp_path):
    """An injected instruction reaching a Bash step must not be able to rule its
    own condition not worth looking at."""
    client, _, _ = _client(tmp_path)
    response = client.post(
        "/v1/rulings", json={"title": TITLE, "verdict": "not_worth_it", "why": "quiet please"}, headers=AGENT
    )
    assert response.status_code == 401


def test_a_malformed_ruling_is_refused(tmp_path):
    client, _, settings = _client(tmp_path)
    for payload in (
        {"title": TITLE, "verdict": "meh", "why": "x"},
        {"title": TITLE, "verdict": "not_worth_it"},
        {"verdict": "not_worth_it", "why": "x"},
    ):
        assert client.post("/v1/rulings", json=payload, headers=AUTH).status_code == 400, payload
    assert not (settings.workdir / "rulings.jsonl").exists()


def test_the_door_has_teeth(tmp_path):
    """The point of filing: with a runbook and a recent real run, the next
    re-fire is answered from the runbook at $0 by the gate that already existed."""
    client, service, settings = _client(tmp_path)
    manifest = settings.workdir / ".claude" / "skills" / slug(TITLE) / "SKILL.md"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(f"# {TITLE}\n\nCheck the sending account.\n\n{CASES_MARKER}\n\n## case 1\n")
    prior = Run(session_key="probe:ww:001", run_id="r-001", status=COMPLETED, text="finding")
    prior.meta = {"title": TITLE, "level": "high", "source": "grafana"}
    prior.finished_at = time.time() - 3600
    service._store.create(prior)

    body = client.post(
        "/v1/rulings",
        json={"title": TITLE, "verdict": "not_worth_it", "why": "known delivery-health condition"},
        headers=AUTH,
    ).json()
    assert body["runbook_present"] is True and "$0" in body["consequence"]

    run = service.start({"message": "alert", "sessionKey": "probe:ww:002", "_meta": {"title": TITLE}}, origin="relay")
    assert run.meta.get("answered_from_runbook") is True
    report = json.loads(run.text)
    assert report["verdict"] == "not_worth_it" and "known delivery-health condition" in report["summary"]
