"""The service receives its own runs' telemetry, keeps a timing record, forwards the rest.

Three rules from hookprobe.telemetry, each pinned: only the CLI this service
launched may post (the per-process header), only for a run it knows (the
session-key attribute), and what is kept is numbers and names — never the text
attributes the content switches can attach. Plus the waterfall the page draws
from it, the guard that keeps the agent out of the file, and retention.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from hookprobe import inputs, telemetry
from hookprobe.app import create_app
from hookprobe.retention import prune
from hookprobe.runs import RunStore
from hookprobe.service import RunService
from tests.helpers import FakeEngine, make_settings

TOKEN = "secret-token"


def _client(tmp_path: Path, **overrides: Any) -> tuple[TestClient, RunService]:
    settings = make_settings(tmp_path, token=TOKEN, **overrides)
    service = RunService(settings, FakeEngine(), RunStore(tmp_path / "results"))
    return TestClient(create_app(settings, service)), service


def _start(client: TestClient, key: str = "probe:inbound:31") -> str:
    r = client.post(
        "/hooks/agent",
        json={"message": "why is /var full", "sessionKey": key},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert r.status_code in (200, 202), r.text
    return key


def _otlp_logs(session_key: str, *records: dict[str, Any]) -> dict[str, Any]:
    def attr(key: str, value: Any) -> dict[str, Any]:
        if isinstance(value, bool):
            return {"key": key, "value": {"boolValue": value}}
        if isinstance(value, int):
            return {"key": key, "value": {"intValue": str(value)}}
        if isinstance(value, float):
            return {"key": key, "value": {"doubleValue": value}}
        return {"key": key, "value": {"stringValue": str(value)}}

    return {
        "resourceLogs": [
            {
                "resource": {
                    "attributes": [
                        attr("service.name", "hookprobe"),
                        attr("hookstack.session_key", session_key),
                    ]
                },
                "scopeLogs": [
                    {
                        "logRecords": [
                            {
                                "timeUnixNano": str(int(rec.pop("ts") * 1e9)),
                                "body": {"stringValue": "claude_code." + rec["event.name"]},
                                "attributes": [attr(k, v) for k, v in rec.items()],
                            }
                            for rec in records
                        ]
                    }
                ],
            }
        ]
    }


NOW = 1_757_000_000.0


def _run_events(key: str) -> dict[str, Any]:
    return _otlp_logs(
        key,
        {
            "ts": NOW + 2.0,
            "event.name": "api_request",
            "model": "gpt-5.6-luna",
            "duration_ms": 1800,
            "cost_usd": 0.12,
            "input_tokens": 25853,
            "output_tokens": 40,
            "cache_read_tokens": 20000,
            "user.email": "someone@example.com",
            "user.id": "u-1",
        },
        {
            "ts": NOW + 3.0,
            "event.name": "tool_result",
            "tool_name": "Bash",
            "duration_ms": 900,
            "success": "true",
            "tool_result": "root 0.0 ... very long output that must not be kept",
        },
        {
            "ts": NOW + 5.5,
            "event.name": "api_request",
            "model": "gpt-5.6-luna",
            "duration_ms": 2000,
            "cost_usd": 0.08,
            "input_tokens": 1400,
            "output_tokens": 1100,
        },
        {
            "ts": NOW + 5.6,
            "event.name": "user_prompt",
            "prompt": "the whole alert text, verbatim",
            "prompt_length": 300,
        },
    )


def test_the_receiver_takes_nothing_without_the_process_header(tmp_path) -> None:
    client, _ = _client(tmp_path)
    key = _start(client)
    body = _run_events(key)
    assert client.post("/otel/v1/logs", json=body).status_code == 401
    assert client.post("/otel/v1/logs", json=body, headers={telemetry.INGEST_HEADER: "guess"}).status_code == 401
    assert client.post("/otel/v1/logs", json=body, headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 401, (
        "the service's bearer token is not the CLI's credential; the two must not be interchangeable"
    )
    ok = client.post("/otel/v1/logs", json=body, headers={telemetry.INGEST_HEADER: telemetry.INGEST_TOKEN})
    assert ok.status_code == 200 and ok.json()["accepted"] == 4


def test_events_are_kept_as_numbers_and_names_never_text(tmp_path) -> None:
    client, _ = _client(tmp_path)
    key = _start(client)
    client.post("/otel/v1/logs", json=_run_events(key), headers={telemetry.INGEST_HEADER: telemetry.INGEST_TOKEN})
    lines = telemetry.read(tmp_path, key)
    assert [line["name"] for line in lines] == ["api_request", "tool_result", "api_request", "user_prompt"]
    first = lines[0]["attrs"]
    assert first["model"] == "gpt-5.6-luna" and first["duration_ms"] == 1800 and first["cost_usd"] == 0.12
    assert first["input_tokens"] == 25853 and first["cache_read_tokens"] == 20000
    assert "user.email" not in first and "user.id" not in first, "the account identity is dropped"
    assert "tool_result" not in lines[1]["attrs"], "tool output is dropped"
    assert "prompt" not in lines[3]["attrs"] and lines[3]["attrs"]["prompt_length"] == 300, (
        "the prompt text goes, its length stays"
    )
    raw = telemetry.path_for(tmp_path, key).read_text()
    assert "very long output" not in raw and "someone@example.com" not in raw


def test_an_unknown_run_gets_no_file(tmp_path) -> None:
    client, _ = _client(tmp_path)
    r = client.post(
        "/otel/v1/logs", json=_run_events("probe:forged:999"), headers={telemetry.INGEST_HEADER: telemetry.INGEST_TOKEN}
    )
    assert r.status_code == 200 and (r.json()["accepted"], r.json()["dropped"]) == (0, 4)
    assert not telemetry.path_for(tmp_path, "probe:forged:999").exists()
    assert not telemetry.telemetry_dir(tmp_path).exists() or not any(telemetry.telemetry_dir(tmp_path).iterdir())


def test_the_waterfall_puts_model_and_tool_calls_on_one_axis(tmp_path) -> None:
    client, _ = _client(tmp_path)
    key = _start(client)
    client.post("/otel/v1/logs", json=_run_events(key), headers={telemetry.INGEST_HEADER: telemetry.INGEST_TOKEN})
    r = client.get(f"/v1/runs/{key}/telemetry", headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200
    body = r.json()
    items = body["waterfall"]
    assert [i["kind"] for i in items] == ["model", "tool", "model"]
    assert items[0]["start"] == round(NOW + 2.0 - 1.8, 3) and items[0]["end"] == NOW + 2.0
    assert items[1]["name"] == "Bash" and items[1]["success"] is True
    s = body["summary"]
    assert s["model_calls"] == 2 and s["model_ms"] == 3800 and s["tool_calls"] == 1 and s["tool_ms"] == 900
    assert s["cost_usd"] == 0.2 and s["models"]["gpt-5.6-luna"]["calls"] == 2
    assert s["span_s"] == round((NOW + 5.5) - (NOW + 2.0 - 1.8), 3)
    assert (
        client.get("/v1/runs/probe:nobody:1/telemetry", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 404
    )
    audit = client.get(f"/v1/runs/{key}/audit", headers={"Authorization": f"Bearer {TOKEN}"}).json()
    assert audit["telemetry"]["model_calls"] == 2, "the accountability record carries the shape too"


def test_a_run_without_telemetry_answers_with_an_empty_waterfall(tmp_path) -> None:
    client, _ = _client(tmp_path)
    key = _start(client)
    r = client.get(f"/v1/runs/{key}/telemetry", headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200 and r.json()["waterfall"] == [] and r.json()["summary"]["events"] == 0


def test_metrics_are_kept_as_points_and_traces_are_accepted_and_dropped(tmp_path) -> None:
    client, _ = _client(tmp_path)
    key = _start(client)
    metrics = {
        "resourceMetrics": [
            {
                "resource": {"attributes": [{"key": "hookstack.session_key", "value": {"stringValue": key}}]},
                "scopeMetrics": [
                    {
                        "metrics": [
                            {
                                "name": "claude_code.token.usage",
                                "sum": {
                                    "dataPoints": [
                                        {
                                            "asInt": "1234",
                                            "timeUnixNano": str(int((NOW + 9) * 1e9)),
                                            "attributes": [
                                                {"key": "type", "value": {"stringValue": "input"}},
                                                {"key": "model", "value": {"stringValue": "gpt-5.6-luna"}},
                                            ],
                                        }
                                    ]
                                },
                            }
                        ]
                    }
                ],
            }
        ]
    }
    r = client.post("/otel/v1/metrics", json=metrics, headers={telemetry.INGEST_HEADER: telemetry.INGEST_TOKEN})
    assert r.status_code == 200 and r.json()["accepted"] == 1
    point = telemetry.read(tmp_path, key)[0]
    assert point == {
        "kind": "metric",
        "name": "token.usage",
        "ts": NOW + 9,
        "value": 1234.0,
        "attrs": {"type": "input", "model": "gpt-5.6-luna"},
    }
    r = client.post(
        "/otel/v1/traces", json={"resourceSpans": []}, headers={telemetry.INGEST_HEADER: telemetry.INGEST_TOKEN}
    )
    assert r.status_code == 200 and (r.json()["accepted"], r.json()["dropped"]) == (0, 0)
    assert (
        client.post("/otel/v1/profiles", json={}, headers={telemetry.INGEST_HEADER: telemetry.INGEST_TOKEN}).status_code
        == 404
    )


def test_a_malformed_body_is_a_400_and_not_an_exception(tmp_path) -> None:
    client, _ = _client(tmp_path)
    r = client.post(
        "/otel/v1/logs",
        content=b"not json",
        headers={telemetry.INGEST_HEADER: telemetry.INGEST_TOKEN, "Content-Type": "application/json"},
    )
    assert r.status_code == 400


def test_bodies_are_forwarded_untouched_when_a_collector_is_named(tmp_path, monkeypatch) -> None:
    sent: list[tuple[str, bytes, str, dict[str, str]]] = []

    def fake_forward(signal: str, raw: bytes, endpoint: str, headers: dict[str, str]) -> bool:
        sent.append((signal, raw, endpoint, headers))
        return True

    monkeypatch.setattr(telemetry, "forward", fake_forward)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://alloy:4318/")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "Authorization=Basic abc, x-scope=hookstack")
    client, _ = _client(tmp_path)
    key = _start(client)
    body = _run_events(key)
    raw = json.dumps(body).encode()
    client.post(
        "/otel/v1/logs",
        content=raw,
        headers={telemetry.INGEST_HEADER: telemetry.INGEST_TOKEN, "Content-Type": "application/json"},
    )
    # forwarding runs on a thread; the test client drains background tasks on response
    deadline = time.time() + 2
    while not sent and time.time() < deadline:
        time.sleep(0.02)
    assert sent, "nothing was forwarded"
    signal, forwarded, endpoint, headers = sent[0]
    assert signal == "logs" and forwarded == raw, "the collector gets the body byte for byte, content switches and all"
    assert endpoint == "http://alloy:4318" and headers == {"Authorization": "Basic abc", "x-scope": "hookstack"}


def test_nothing_is_forwarded_when_no_collector_is_named(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    called = []
    monkeypatch.setattr(telemetry, "forward", lambda *a: called.append(a) or True)
    client, _ = _client(tmp_path)
    key = _start(client)
    client.post("/otel/v1/logs", json=_run_events(key), headers={telemetry.INGEST_HEADER: telemetry.INGEST_TOKEN})
    time.sleep(0.05)
    assert not called


def test_the_subprocess_is_pointed_at_this_service(tmp_path) -> None:
    on = telemetry.subprocess_env(make_settings(tmp_path, port=8088, telemetry_receiver="on"))
    assert on["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://127.0.0.1:8088/otel"
    assert on["OTEL_EXPORTER_OTLP_PROTOCOL"] == "http/json" and on["OTEL_LOGS_EXPORTER"] == "otlp"
    assert on["OTEL_EXPORTER_OTLP_HEADERS"] == f"{telemetry.INGEST_HEADER}={telemetry.INGEST_TOKEN}"
    assert on["CLAUDE_CODE_ENABLE_TELEMETRY"] == "1"
    assert telemetry.subprocess_env(make_settings(tmp_path, telemetry_receiver="off")) == {}


def test_the_agent_may_not_write_its_own_telemetry(tmp_path) -> None:
    reason = inputs.write_deny_reason("telemetry/probe:inbound:31.jsonl", workdir=tmp_path)
    assert reason is not None and "steers the next run" in reason
    assert inputs.write_deny_reason("scratch/notes.md", workdir=tmp_path) is None


def test_retention_prunes_old_telemetry(tmp_path) -> None:
    workdir = tmp_path / "wd"
    home = tmp_path / "home"
    telemetry.append(workdir, "probe:old:1", [{"kind": "event", "name": "api_request", "ts": 1.0, "attrs": {}}])
    telemetry.append(workdir, "probe:new:2", [{"kind": "event", "name": "api_request", "ts": 2.0, "attrs": {}}])
    stale = time.time() - 10 * 86400
    os.utime(telemetry.path_for(workdir, "probe:old:1"), (stale, stale))
    assert prune(workdir, home, 7) == 1
    assert not telemetry.path_for(workdir, "probe:old:1").exists()
    assert telemetry.path_for(workdir, "probe:new:2").exists()
