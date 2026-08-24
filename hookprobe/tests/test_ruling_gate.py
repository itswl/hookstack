"""A standing not_worth_it ruling answers repeats from the runbook, for free.

Measured before the gate existed: one condition (a Grafana folder mapping
NoData to Alerting) was ruled not_worth_it twice and then investigated ten more
times at full price, each run re-deriving the same sentence. The verdict was
recorded, displayed, and consulted by nothing. These tests are the "consulted"
part — and just as much the reasons NOT to skip: every clause of the gate has a
test that holds it open.
"""

from __future__ import annotations

import json
import time

from fastapi.testclient import TestClient

from hookprobe import rulings
from hookprobe.app import create_app
from hookprobe.distill import CASES_MARKER, slug
from hookprobe.runs import RunStore
from hookprobe.service import RunService, alert_meta_from_prompt
from tests.helpers import FakeEngine, make_settings

TOKEN = "secret-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
TITLE = "DatasourceNoData"

# The shape a platform prompt actually has: an instruction preamble, an OUTPUT
# template whose values are all "unknown", and the alert itself as JSON.
PROMPT = (
    "你是无人值守 SRE 分析 Agent。输出模板：\n"
    '{"identity": {"rule_name": "unknown", "source": "unknown", "severity": "unknown"}}\n'
    "## 当前告警关键字段\n"
    '```json\n{"source":"grafana","rule_name":"DatasourceNoData","level":"high","summary":"value=null"}\n```\n'
)


def poll_until_final(client: TestClient, session_key: str, deadline: float = 3.0):
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        response = client.get(f"/sessions/{session_key}/final", headers=AUTH)
        if response.status_code == 200 and response.json().get("isFinal"):
            return response.json()
        time.sleep(0.02)
    raise AssertionError(f"{session_key} never finalised")


def _seed(workdir, *, verdict: str = "not_worth_it", age_days: float = 1.0) -> None:
    rulings.record_local(
        workdir,
        [{"identity": f"ww|{TITLE}|origin=grafana", "verdict": verdict, "why": "alarm-test maps NoData to Alerting"}],
        model="m",
    )
    if age_days:
        path = workdir / "rulings.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        for row in rows:
            row["at"] = time.time() - age_days * 86400
        path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def _install_runbook(workdir) -> None:
    manifest = workdir / ".claude" / "skills" / slug(TITLE) / "SKILL.md"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(f"# {TITLE}\n\nCheck the alarm-test folder's NoData mapping.\n\n{CASES_MARKER}\n\n## case 1\n")


def _client(tmp_path) -> tuple[TestClient, RunService]:
    settings = make_settings(tmp_path, token=TOKEN)
    service = RunService(settings, FakeEngine(), RunStore(tmp_path / "results"))
    return TestClient(create_app(settings, service)), service


def _full_run(client: TestClient, key: str) -> dict:
    client.post("/hooks/agent", json={"message": PROMPT, "sessionKey": key}, headers=AUTH)
    return poll_until_final(client, key)


def test_the_gate_answers_a_ruled_useless_condition_from_the_runbook(tmp_path) -> None:
    client, service = _client(tmp_path)
    _seed(tmp_path)
    _install_runbook(tmp_path)
    _full_run(client, "seed-1")  # the recent REAL run the gate requires

    final = (
        poll_until_final(client, "gated-1")
        if client.post("/hooks/agent", json={"message": PROMPT, "sessionKey": "gated-1"}, headers=AUTH)
        else None
    )
    report = json.loads(final["text"])
    assert report["answered_from_runbook"] is True
    assert "not_worth_it" in final["text"] and "alarm-test" in report["summary"]

    run = service.get("gated-1")
    assert run.cost_usd == 0.0, "the whole point"
    assert run.meta["title"] == TITLE, "derived from the prompt, not stated by the caller"
    assert run.distilled["skipped"], "a gated run must never distil a runbook about itself"


def test_every_clause_that_holds_the_gate_open(tmp_path) -> None:
    """force, a worth_it verdict, a stale ruling, a missing runbook, and a
    condition with no recent real run each cause a paid investigation."""
    engine_text = '{"summary": "ok"}'  # what FakeEngine answers

    # force: the caller explicitly asks for a real run.
    client, _ = _client(tmp_path)
    _seed(tmp_path)
    _install_runbook(tmp_path)
    _full_run(client, "seed-1")
    client.post("/hooks/agent", json={"message": PROMPT, "sessionKey": "forced", "force": True}, headers=AUTH)
    assert poll_until_final(client, "forced")["text"] == engine_text

    # worth_it: the latest ruling wins, and it says the condition deserves eyes.
    _seed(tmp_path, verdict="worth_it", age_days=0)
    client.post("/hooks/agent", json={"message": PROMPT, "sessionKey": "worth"}, headers=AUTH)
    assert poll_until_final(client, "worth")["text"] == engine_text

    # stale: older than the TTL means nobody has defended it lately.
    client2, _ = _client(tmp_path / "b")
    _seed(tmp_path / "b", age_days=30)
    _install_runbook(tmp_path / "b")
    _full_run(client2, "seed-1")
    client2.post("/hooks/agent", json={"message": PROMPT, "sessionKey": "stale"}, headers=AUTH)
    assert poll_until_final(client2, "stale")["text"] == engine_text

    # no runbook: there is nothing to answer FROM.
    client3, _ = _client(tmp_path / "c")
    _seed(tmp_path / "c")
    _full_run(client3, "seed-1")
    client3.post("/hooks/agent", json={"message": PROMPT, "sessionKey": "nobook"}, headers=AUTH)
    assert poll_until_final(client3, "nobook")["text"] == engine_text

    # no recent real run: the evidence must be re-earned, not cited forever.
    client4, _ = _client(tmp_path / "d")
    _seed(tmp_path / "d")
    _install_runbook(tmp_path / "d")
    client4.post("/hooks/agent", json={"message": PROMPT, "sessionKey": "first-ever"}, headers=AUTH)
    assert poll_until_final(client4, "first-ever")["text"] == engine_text


def test_gated_answers_do_not_satisfy_the_reverify_requirement(tmp_path) -> None:
    """Ten gated answers in a row must not count as 'recently verified' — only
    a run that actually LOOKED counts, or the gate feeds itself forever."""
    client, service = _client(tmp_path)
    _seed(tmp_path, age_days=0)
    _install_runbook(tmp_path)
    _full_run(client, "seed-1")
    poll = lambda k: poll_until_final(client, k)  # noqa: E731
    client.post("/hooks/agent", json={"message": PROMPT, "sessionKey": "g1"}, headers=AUTH)
    assert json.loads(poll("g1")["text"]).get("answered_from_runbook") is True

    # Age the REAL run out of the window; the gated g1 stays recent.
    real = service.get("seed-1")
    real.finished_at = time.time() - 8 * 86400
    client.post("/hooks/agent", json={"message": PROMPT, "sessionKey": "g2"}, headers=AUTH)
    assert poll("g2")["text"] == '{"summary": "ok"}', "a gated run must not stand in for a real one"


def test_prompt_meta_extraction_skips_the_template_and_prose(tmp_path) -> None:
    meta = alert_meta_from_prompt(PROMPT)
    assert meta == {"title": TITLE, "source": "grafana", "level": "high"}
    assert alert_meta_from_prompt("just words, no alert") == {}
    # Prose descriptions must not pass the charset: this is the real failure the
    # constraint was written against.
    prose = '"source": "来源系统；未知则 unknown", "level": "critical | high | medium"'
    assert alert_meta_from_prompt(prose) == {}


def test_standing_returns_the_latest_and_respects_the_ttl(tmp_path) -> None:
    rulings.record_local(tmp_path, [{"identity": f"a|{TITLE}", "verdict": "not_worth_it", "why": "x"}], model="m")
    rulings.record_local(tmp_path, [{"identity": f"a|{TITLE}", "verdict": "worth_it", "why": "y"}], model="m")
    assert rulings.standing(tmp_path, TITLE, ttl_days=14)["verdict"] == "worth_it", "latest wins"
    assert rulings.standing(tmp_path, "other", ttl_days=14) is None
    assert rulings.standing(tmp_path, TITLE, ttl_days=0) is None, "0 turns the gate off"
    assert rulings.condition_of("ww|X|origin=g") == "X" and rulings.condition_of("g|Y") == "Y"
