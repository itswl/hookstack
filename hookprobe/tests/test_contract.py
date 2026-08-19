"""The contract an OpenClaw-dialect client actually exercises.

Payload shapes mirror what such a client POSTs to trigger an analysis and
how it polls /final; change these tests only together with the callers that
speak this dialect.
"""

from __future__ import annotations

import json
import time

from fastapi.testclient import TestClient

from hookprobe import distill
from hookprobe.app import create_app
from hookprobe.runs import RunStore
from hookprobe.service import RunService
from tests.helpers import FakeEngine, GatedEngine, make_settings

TOKEN = "secret-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

# What an OpenClaw-dialect client actually POSTs.
TRIGGER_PAYLOAD = {
    "message": "...deep analysis prompt + alert payload...",
    "name": "deep-analysis",
    "sessionKey": "hook:deep-analysis:grafana:abc-123",
    "wakeMode": "now",
    "deliver": False,
    "thinking": "high",
    "timeoutSeconds": 900,
}


def make_client(tmp_path, engine) -> TestClient:
    settings = make_settings(tmp_path, token=TOKEN)
    service = RunService(settings, engine, RunStore(tmp_path / "results"))
    return TestClient(create_app(settings, service))


def poll_until_final(client: TestClient, session_key: str, deadline: float = 3.0):
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        response = client.get(f"/sessions/{session_key}/final", headers=AUTH)
        if response.status_code == 200:
            return response
        assert response.status_code == 202, response.text
        time.sleep(0.02)
    raise AssertionError("final result never arrived")


def test_healthz_is_open(tmp_path) -> None:
    with make_client(tmp_path, FakeEngine()) as client:
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


def test_auth_is_required_on_contract_routes(tmp_path) -> None:
    with make_client(tmp_path, FakeEngine()) as client:
        assert client.post("/hooks/agent", json=TRIGGER_PAYLOAD).status_code == 401
        bad = {"Authorization": "Bearer wrong"}
        assert client.post("/hooks/agent", json=TRIGGER_PAYLOAD, headers=bad).status_code == 401
        assert client.get("/sessions/x/final").status_code == 401
        assert client.get("/v1/runs/x", headers=bad).status_code == 401


def test_a_high_byte_in_a_credential_header_is_a_refusal_not_a_crash(tmp_path) -> None:
    """hmac.compare_digest raises TypeError on a str holding non-ASCII, and
    Starlette decodes header bytes as latin-1 — so one 0xF6 byte in the bearer
    token or the pipe's signature used to answer HTTP 500 to a caller holding
    no credential at all. A wrong credential is a refusal whatever bytes it is
    made of."""
    with make_client(tmp_path, FakeEngine()) as client:
        bearer = client.get("/v1/runs", headers={b"Authorization": b"Bearer t\xf6ken"})
        assert bearer.status_code == 401

        # The event door verifies a signature rather than a token, and it is
        # reachable without one — so its crash was the cheaper of the two.
        signed = client.post(
            "/hooks/event",
            json={"source": "grafana", "title": "x", "body": "y", "level": "critical", "event_id": "e1"},
            headers={b"X-Hook-Signature": b"t\xf6ken", b"X-Hook-Timestamp": str(int(time.time())).encode()},
        )
        assert signed.status_code != 500


def test_trigger_then_poll_roundtrip(tmp_path) -> None:
    engine = GatedEngine()
    with make_client(tmp_path, engine) as client:
        trigger = client.post("/hooks/agent", json=TRIGGER_PAYLOAD, headers=AUTH)
        assert trigger.status_code == 200
        body = trigger.json()
        assert body["runId"]
        assert body["sessionKey"] == TRIGGER_PAYLOAD["sessionKey"]

        # Still running: the poller treats 202 as "analysis in progress".
        pending = client.get(f"/sessions/{body['sessionKey']}/final", headers=AUTH)
        assert pending.status_code == 202

        engine.release.set()
        final = poll_until_final(client, body["sessionKey"])
        payload = final.json()
        assert payload["isFinal"] is True
        assert payload["text"] == '{"summary": "done"}'
        assert payload["messageCount"] == 2


def test_retrigger_returns_same_run(tmp_path) -> None:
    engine = GatedEngine()
    with make_client(tmp_path, engine) as client:
        first = client.post("/hooks/agent", json=TRIGGER_PAYLOAD, headers=AUTH).json()
        second = client.post("/hooks/agent", json=TRIGGER_PAYLOAD, headers=AUTH).json()
        assert first["runId"] == second["runId"]
        engine.release.set()
        poll_until_final(client, first["sessionKey"])


def test_unknown_session_is_404(tmp_path) -> None:
    with make_client(tmp_path, FakeEngine()) as client:
        response = client.get("/sessions/hook:never-seen/final", headers=AUTH)
        assert response.status_code == 404


def test_empty_message_is_400(tmp_path) -> None:
    with make_client(tmp_path, FakeEngine()) as client:
        response = client.post("/hooks/agent", json={"sessionKey": "hook:x"}, headers=AUTH)
        assert response.status_code == 400


def test_continue_endpoint_roundtrip(tmp_path) -> None:
    with make_client(tmp_path, FakeEngine()) as client:
        body = client.post("/hooks/agent", json=TRIGGER_PAYLOAD, headers=AUTH).json()
        key = body["sessionKey"]
        poll_until_final(client, key)

        # Guards: busy sessions conflict, unknown sessions 404, auth required.
        assert client.post(f"/sessions/{key}/continue", json={"message": "x"}).status_code == 401
        assert client.post("/sessions/hook:nope/continue", json={"message": "x"}, headers=AUTH).status_code == 404
        assert client.post(f"/sessions/{key}/continue", json={"message": ""}, headers=AUTH).status_code == 400

        followup = client.post(f"/sessions/{key}/continue", json={"message": "check node capacity"}, headers=AUTH)
        assert followup.status_code == 200
        assert followup.json()["sessionKey"] == key

        final = poll_until_final(client, key)
        assert final.json()["isFinal"] is True

        detail = client.get(f"/v1/runs/{key}", headers=AUTH).json()
        assert detail["engine_session_id"] == "sdk-session-1"
        assert len(detail["turns"]) == 2
        assert detail["turns"][1]["message"] == "check node capacity"


def test_detail_reports_the_prompt_files_as_they_stand_now(tmp_path) -> None:
    """A recorded digest says nothing alone; the read path supplies what to compare it to."""
    from hookprobe.engine import file_fact

    (tmp_path / "CLAUDE.md").write_text("Report in English.", encoding="utf-8")
    with make_client(tmp_path, FakeEngine()) as client:
        key = TRIGGER_PAYLOAD["sessionKey"]
        client.post("/hooks/agent", json=TRIGGER_PAYLOAD, headers=AUTH)
        poll_until_final(client, key)

        before = client.get(f"/v1/runs/{key}", headers=AUTH).json()["inputs_now"]
        assert before["memory"] == file_fact(tmp_path / "CLAUDE.md")["sha256"]
        # Absent is a state of its own, not a missing key: the console says "absent".
        assert before["system_prompt_append"] is None

        client.put("/v1/memory", json={"content": "Report in Chinese."}, headers=AUTH)
        after = client.get(f"/v1/runs/{key}", headers=AUTH).json()["inputs_now"]
        assert after["memory"] != before["memory"]

        # Both ends of the comparison have to stay wired together.
        assert "inputs_now" in client.get("/ui").text


def test_run_list_and_ui_page(tmp_path) -> None:
    with make_client(tmp_path, FakeEngine()) as client:
        # The list needs auth; the page markup does not (it holds no data).
        assert client.get("/v1/runs").status_code == 401
        page = client.get("/ui")
        assert page.status_code == 200
        assert "hookprobe" in page.text
        assert client.get("/", follow_redirects=False).status_code in (302, 307)

        client.post("/hooks/agent", json=TRIGGER_PAYLOAD, headers=AUTH)
        second = dict(TRIGGER_PAYLOAD, sessionKey="web:manual-1", message="check disk usage on node-3")
        client.post("/hooks/agent", json=second, headers=AUTH)
        poll_until_final(client, TRIGGER_PAYLOAD["sessionKey"])
        poll_until_final(client, "web:manual-1")

        runs = client.get("/v1/runs", headers=AUTH).json()
        assert {r["session_key"] for r in runs} == {TRIGGER_PAYLOAD["sessionKey"], "web:manual-1"}
        manual = next(r for r in runs if r["session_key"] == "web:manual-1")
        assert manual["status"] == "completed"
        assert manual["turn_count"] == 1
        assert manual["model"] == "claude-opus-5"
        assert manual["title"].startswith("check disk usage")

        detail = client.get("/v1/runs/web:manual-1", headers=AUTH).json()
        assert detail["turns"][0]["usage"]["input_tokens"] == 12


def test_run_list_includes_persisted_runs_after_restart(tmp_path) -> None:
    with make_client(tmp_path, FakeEngine()) as client:
        client.post("/hooks/agent", json=TRIGGER_PAYLOAD, headers=AUTH)
        poll_until_final(client, TRIGGER_PAYLOAD["sessionKey"])

    # New app over the same results dir — the finished run must reappear.
    with make_client(tmp_path, FakeEngine()) as reborn:
        runs = reborn.get("/v1/runs", headers=AUTH).json()
        assert [r["session_key"] for r in runs] == [TRIGGER_PAYLOAD["sessionKey"]]
        detail = reborn.get(f"/v1/runs/{TRIGGER_PAYLOAD['sessionKey']}", headers=AUTH).json()
        assert len(detail["turns"]) == 1


def test_skills_endpoints(tmp_path) -> None:
    skill_dir = tmp_path / ".claude" / "skills" / "gpu-card-alert-triage"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        '---\nname: gpu-card-alert-triage\ndescription: "Triage GPU card alerts"\n---\n\n'
        "## Steps\n1. check nvidia-smi\n",
        encoding="utf-8",
    )
    (tmp_path / ".claude" / "skills" / "not-a-skill").mkdir()  # no SKILL.md — ignored

    with make_client(tmp_path, FakeEngine()) as client:
        assert client.get("/v1/skills").status_code == 401

        skills = client.get("/v1/skills", headers=AUTH).json()
        assert [s["name"] for s in skills] == ["gpu-card-alert-triage"]
        assert skills[0]["description"] == "Triage GPU card alerts"
        assert "SKILL.md" in skills[0]["files"]

        detail = client.get("/v1/skills/gpu-card-alert-triage", headers=AUTH).json()
        assert "check nvidia-smi" in detail["content"]

        # Names that could walk the filesystem never resolve.
        assert client.get("/v1/skills/..", headers=AUTH).status_code == 404
        assert client.get("/v1/skills/%2e%2e%2fsecrets", headers=AUTH).status_code == 404
        assert client.get("/v1/skills/missing", headers=AUTH).status_code == 404


def test_continue_while_running_is_409(tmp_path) -> None:
    engine = GatedEngine()
    with make_client(tmp_path, engine) as client:
        body = client.post("/hooks/agent", json=TRIGGER_PAYLOAD, headers=AUTH).json()
        conflict = client.post(f"/sessions/{body['sessionKey']}/continue", json={"message": "x"}, headers=AUTH)
        assert conflict.status_code == 409
        engine.release.set()
        poll_until_final(client, body["sessionKey"])


def test_stop_endpoint_settles_the_run(tmp_path) -> None:
    engine = GatedEngine()  # never released — stop is the only way out
    with make_client(tmp_path, engine) as client:
        body = client.post("/hooks/agent", json=TRIGGER_PAYLOAD, headers=AUTH).json()
        key = body["sessionKey"]
        assert client.post(f"/sessions/{key}/stop").status_code == 401
        assert client.post("/sessions/hook:nope/stop", headers=AUTH).status_code == 404

        stopping = client.post(f"/sessions/{key}/stop", headers=AUTH)
        assert stopping.status_code == 200

        final = poll_until_final(client, key)
        report = json.loads(final.json()["text"])
        assert "stopped by operator" in report["summary"]
        # Nothing left to stop now.
        assert client.post(f"/sessions/{key}/stop", headers=AUTH).status_code == 409


def test_memory_endpoints(tmp_path) -> None:
    with make_client(tmp_path, FakeEngine()) as client:
        assert client.get("/v1/memory").status_code == 401
        empty = client.get("/v1/memory", headers=AUTH).json()
        assert empty["content"] == ""

        saved = client.put("/v1/memory", json={"content": "# env\nprod cluster lives in cn-north"}, headers=AUTH)
        assert saved.status_code == 200 and saved.json()["saved"] is True
        back = client.get("/v1/memory", headers=AUTH).json()
        assert "cn-north" in back["content"]
        assert back["path"].endswith("CLAUDE.md")

        assert client.put("/v1/memory", json={"content": 42}, headers=AUTH).status_code == 400
        too_big = "x" * (256 * 1024 + 1)
        assert client.put("/v1/memory", json={"content": too_big}, headers=AUTH).status_code == 413


def test_engine_failure_is_served_as_final_report(tmp_path) -> None:
    with make_client(tmp_path, FakeEngine(exc=RuntimeError("engine exploded"))) as client:
        body = client.post("/hooks/agent", json=TRIGGER_PAYLOAD, headers=AUTH).json()
        final = poll_until_final(client, body["sessionKey"])
        payload = final.json()
        assert payload["isFinal"] is True
        report = json.loads(payload["text"])
        assert "hookprobe run failed" in report["summary"]

        detail = client.get(f"/v1/runs/{body['sessionKey']}", headers=AUTH).json()
        assert detail["status"] == "failed"
        assert "engine exploded" in detail["error"]


def test_stream_pushes_the_steps_of_a_running_turn(tmp_path) -> None:
    """The console's live feed: steps arrive as they happen, not on the next tick."""
    events = [
        {"type": "tool_use", "name": "Bash", "detail": "kubectl get pods"},
        {"type": "text", "text": "checking the gateway"},
    ]
    engine = FakeEngine(events=events, delay=0.15)
    seen: list[dict] = []
    with make_client(tmp_path, engine) as client:
        client.post("/hooks/agent", json={"message": "investigate", "sessionKey": "s-live"}, headers=AUTH)
        with client.stream("GET", "/v1/runs/s-live/stream", headers=AUTH) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("application/x-ndjson")
            for line in response.iter_lines():
                if not line.strip():
                    continue
                seen.append(json.loads(line))
                if seen[-1]["type"] == "done":
                    break

    kinds = [item["type"] for item in seen]
    assert kinds[0] == "snapshot", seen
    assert kinds[-1] == "done"
    # Every step reached the watcher, whether through the opening snapshot or
    # pushed afterwards — which is the guarantee, not which of the two.
    delivered = list(seen[0].get("events") or []) + [item for item in seen[1:] if item["type"] in {"tool_use", "text"}]
    assert [item.get("name") or item.get("text") for item in delivered] == [
        "Bash",
        "checking the gateway",
    ], delivered


def test_stream_closes_immediately_for_a_finished_run(tmp_path) -> None:
    with make_client(tmp_path, FakeEngine()) as client:
        client.post("/hooks/agent", json={"message": "go", "sessionKey": "s-done"}, headers=AUTH)
        poll_until_final(client, "s-done")
        with client.stream("GET", "/v1/runs/s-done/stream", headers=AUTH) as response:
            payloads = [json.loads(line) for line in response.iter_lines() if line.strip()]

    assert [item["type"] for item in payloads] == ["snapshot", "done"]
    assert payloads[-1]["status"] == "completed"


def test_stream_needs_the_token_and_a_real_session(tmp_path) -> None:
    with make_client(tmp_path, FakeEngine()) as client:
        assert client.get("/v1/runs/s-missing/stream", headers=AUTH).status_code == 404
        assert client.get("/v1/runs/s-missing/stream").status_code == 401


def test_watchers_are_released_when_the_reader_goes_away(tmp_path) -> None:
    """A closed browser tab must not leave a queue growing behind it."""
    settings = make_settings(tmp_path, token=TOKEN)
    service = RunService(settings, FakeEngine(), RunStore(tmp_path / "results"))
    with TestClient(create_app(settings, service)) as client:
        client.post("/hooks/agent", json={"message": "go", "sessionKey": "s-leak"}, headers=AUTH)
        poll_until_final(client, "s-leak")
        with client.stream("GET", "/v1/runs/s-leak/stream", headers=AUTH) as response:
            list(response.iter_lines())

    assert service._watchers == {}


def test_a_finished_run_drafts_a_skill_but_never_saves_one(tmp_path) -> None:
    """This endpoint drafts and never saves, whatever else is switched on.

    It is the operator's path: read it, prune the dead ends the record cannot
    tell from the useful steps, then save. The automatic loop
    (HOOKPROBE_AUTO_DISTILL_MAX, off here) is a separate door, and turning it on
    must not turn this one into a write.
    """
    with make_client(tmp_path, FakeEngine()) as client:
        key = TRIGGER_PAYLOAD["sessionKey"]
        assert client.post(f"/v1/runs/{key}/distill", headers=AUTH).status_code == 404

        client.post("/hooks/agent", json=TRIGGER_PAYLOAD, headers=AUTH)
        poll_until_final(client, key)

        draft = client.post(f"/v1/runs/{key}/distill", headers=AUTH)
        assert draft.status_code == 200
        body = draft.json()
        assert body["content"].startswith("---\nname: ")
        assert body["name"] and "/" not in body["name"]
        # One delimited case, in the region later investigations append to.
        assert distill.CASES_MARKER in body["content"]
        assert body["content"].count("<!-- case:start") == 1
        # And the volume is untouched: the draft is not a skill until saved.
        assert client.get(f"/v1/skills/{body['name']}", headers=AUTH).status_code == 404

        # Saving is the operator's existing action, and then it exists.
        client.put(f"/v1/skills/{body['name']}", json={"content": body["content"]}, headers=AUTH)
        assert client.get(f"/v1/skills/{body['name']}", headers=AUTH).status_code == 200


def test_a_draft_is_named_after_the_alert_not_the_instructions(tmp_path) -> None:
    """The family loop's first message is the caller's whole prompt.

    Its first line is a role description, so naming the runbook after it
    produced `webhookwise-sre-agent-webhook-webhook` — measured on a real
    production run, not imagined. The alert is on the Title: line the loop
    writes, and the report that comes back is JSON, so quoting its opening
    paragraph put a whole document under "what it turned out to be".
    """
    from hookprobe.distill import draft_skill

    class _Run:
        session_key = "hook:deep-analysis:grafana:abc"
        engine_session_id = "sdk-1"
        current_message = ""
        turns = [
            {
                "message": (
                    "You are the unattended SRE deep-analysis agent. Nobody will follow up.\n"
                    "Source: grafana\nLevel: critical\nTitle: [MQ] Ready backlog growing\nBody: ...\n"
                ),
                "text": '{"summary": "Consumers stopped draining the queue", "root_cause": "consumer crash"}',
                "events": [{"type": "tool_use", "name": "Bash", "detail": "command -v rabbitmqctl"}],
            }
        ]

    draft = draft_skill(_Run())

    assert draft["name"] == "mq-ready-backlog-growing"
    assert "[MQ] Ready backlog growing" in draft["content"]
    assert "unattended SRE" not in draft["content"]
    # The human-readable field, not the whole object.
    assert "Consumers stopped draining the queue" in draft["content"]
    assert '"root_cause"' not in draft["content"]


def test_a_draft_survives_a_report_that_does_not_quite_parse(tmp_path) -> None:
    """One malformed line must not cost the whole draft.

    A real production report was 4,671 characters, correctly terminated, and
    invalid at line 20 — the model emitted one bad entry. Strict parsing failed,
    so the draft fell back to quoting the opening brace and naming itself after
    the caller's role description.
    """
    from hookprobe.distill import draft_skill

    class _Run:
        session_key = "hook:deep-analysis:grafana:xyz"
        engine_session_id = "sdk-2"
        current_message = ""
        turns = [
            {
                "message": "你是无人值守 SRE Agent。\n## 输入数据\n...",
                "text": (
                    '{\n  "alert_identity": {\n    "rule_name": "[MQ] Ready backlog growing"\n  },\n'
                    "  oops-not-json,\n"
                    '  "summary": "Consumers stopped draining the queue"\n}'
                ),
                "events": [],
            }
        ]

    draft = draft_skill(_Run())

    assert draft["name"] == "mq-ready-backlog-growing"
    assert "Consumers stopped draining the queue" in draft["content"]
    assert "无人值守" not in draft["content"]


def test_metrics_speak_prometheus_and_need_no_token(tmp_path) -> None:
    """The platform's alerting is the consumer; a scraper cannot type a token
    prompt, and nothing here is more sensitive than counts and a dollar figure."""
    with make_client(tmp_path, FakeEngine()) as client:
        client.post("/hooks/agent", json=TRIGGER_PAYLOAD, headers=AUTH)
        poll_until_final(client, TRIGGER_PAYLOAD["sessionKey"])

        response = client.get("/metrics")

        assert response.status_code == 200
        assert "text/plain" in response.headers["content-type"]
        body = response.text
        assert "hookprobe_up 1" in body
        assert "hookprobe_active_runs 0" in body
        # The fake engine's run cost 0.5 inside the (disabled) window.
        assert "hookprobe_window_spend_usd 0.5" in body
        assert "hookprobe_budget_usd" not in body, "no budget configured, no budget series"
        assert "hookprobe_runbooks 0" in body
