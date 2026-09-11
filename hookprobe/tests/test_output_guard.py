"""What came BACK from a tool, which nothing used to look at.

The input guard refuses changes, so `kubectl get secret db-creds -o yaml` and
`env` are both allowed under `readonly` — and they should be: an investigator
that cannot read cannot investigate. What was missing is that nothing then
examined the answer, so a run whose context was filled with a live credential
was indistinguishable from one that read a pod list. Nobody could decide to
discard the report or rotate the key, because nobody knew.

Borrowed in idea from `ToolOutputGuardrail` in openai/openai-agents-python.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hookprobe import audit, gate


def test_the_input_guard_allows_reading_a_secret_which_is_the_whole_point(tmp_path: Path) -> None:
    """Pinned so the premise of this file cannot quietly change: these are
    reads, `readonly` permits reads, and that is correct."""
    for command in ("kubectl get secret db-creds -o yaml", "env"):
        assert gate.deny_reason("Bash", {"command": command}, bash_mode="readonly", workdir=tmp_path) is None


@pytest.mark.parametrize(
    ("kind", "output"),
    [
        ("private key", "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAK\n-----END RSA PRIVATE KEY-----"),
        ("aws access key id", "aws_access_key_id = AKIAIOSFODNN7EXAMPLE"),
        ("github token", "GH_TOKEN=ghp_" + "a" * 36),
        ("openai-style api key", "ANTHROPIC_AUTH_TOKEN=sk-" + "b" * 40),
        ("slack token", "SLACK=xoxb-1234567890-abcdefghij"),
    ],
)
def test_a_credential_in_tool_output_is_named_by_kind(kind: str, output: str) -> None:
    found = gate.output_reason({"stdout": output})
    assert found == kind
    # The KIND, never the match: this string reaches the audit file and, on some
    # deployments, a chat card. A guard that quoted what it found would be the
    # leak it exists to record.
    assert output.split("=")[-1][:12] not in (found or "")


@pytest.mark.parametrize(
    "output",
    [
        "NAME  READY  STATUS   RESTARTS  AGE\napi-7f9  1/1  Running  0  4d",
        # The words, without a credential. A guard that fired on these is a
        # guard people learn to ignore — every Kubernetes manifest has them.
        "spec:\n  containers:\n  - env:\n    - name: DB_PASSWORD\n      valueFrom:\n        secretKeyRef:",
        "error: secret 'db-creds' not found",
        "",
    ],
)
def test_ordinary_output_is_not_flagged(output: str) -> None:
    assert gate.output_reason({"stdout": output}) is None


def test_a_flagged_answer_lands_in_the_audit_beside_the_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The record is what this buys. Blocking is not attempted: by PostToolUse
    the output has already reached the model, so a denial there would be
    theatre — and no shape for withholding it is agreed across the three
    runtimes."""
    audit_dir = tmp_path / "audit"
    monkeypatch.setenv("HOOKPROBE_GATE_AUDIT", str(audit_dir))
    monkeypatch.setenv("HOOKPROBE_GATE_MODE", "readonly")
    monkeypatch.setenv("HOOKPROBE_GATE_WORKDIR", str(tmp_path))
    monkeypatch.setenv("HOOKPROBE_SESSION_KEY", "probe:leak:1")

    for response in ({"stdout": "AKIAIOSFODNN7EXAMPLE"}, {"stdout": "api-7f9 Running"}):
        gate.decide(
            {
                "hook_event_name": "PostToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "kubectl get secret db-creds -o yaml"},
                "tool_response": response,
            },
            dict(__import__("os").environ),
        )

    lines = [
        json.loads(raw)
        # `*.jsonl`, not the first thing `iterdir` happens to yield. The audit
        # directory gained a `.chain` file on 2026-09-10 and directory order is
        # the filesystem's: macOS yielded the day file first and Linux yielded
        # `.chain`, so this passed on every local gate and failed in CI, where
        # the hash was handed to `json.loads`. Every source path already globs
        # `*.jsonl`; this test was the only reader that did not say what it meant.
        for raw in sorted(audit_dir.glob("*.jsonl"))[0].read_text().splitlines()
        if raw
    ]
    assert [line.get("output_secret") for line in lines] == ["aws access key id", None]
    assert all(line["tool"] == "Bash" for line in lines)
    # The command is recorded as before — the flag is added, nothing replaced.
    assert "kubectl get secret" in lines[0]["detail"]


def test_repeated_refusals_are_counted_where_one_refusal_used_to_look_the_same(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The third outcome the gate did not have.

    `deny_reason` could refuse and continue, and that was all — so a turn the
    posture refused twenty times left the same trace as one refused once and
    rephrased. Those are different runs: the first is an agent being steered,
    the second is an agent narrowing a query. Counted from the audit because
    the gate is stateless on the spawned runtimes, where it is a fresh process
    per tool call.
    """
    audit_dir = tmp_path / "audit"
    env = {
        "HOOKPROBE_GATE_AUDIT": str(audit_dir),
        "HOOKPROBE_GATE_MODE": "readonly",
        "HOOKPROBE_GATE_WORKDIR": str(tmp_path),
        "HOOKPROBE_SESSION_KEY": "probe:steered:1",
    }
    started = __import__("time").time()
    assert audit.trips(audit_dir, "probe:steered:1", since=started) == 0, "nothing refused yet"

    for command in ("kubectl delete pod a", "kubectl apply -f x.yaml", "kubectl get pods"):
        gate.decide(
            {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command}},
            env,
        )

    assert audit.trips(audit_dir, "probe:steered:1", since=started) == 2, "two refused, one allowed"
    # Scoped to the session and the turn, or a busy node's counts would bleed.
    assert audit.trips(audit_dir, "probe:other:1", since=started) == 0
    assert audit.trips(audit_dir, "probe:steered:1", since=started + 3600) == 0


def test_both_new_numbers_have_a_reader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The complaint this file's own note made, applied to this file's own work.

    `guard_trips` and `output_secret` were both landing somewhere nobody looks
    — a turn record and a JSONL. To learn that a run's context had been filled
    with a live key you had to SSH in and grep, which is exactly when you no
    longer need to know. The count is on the run SUMMARY (so the board can show
    it) and the kinds are on the DETAIL (one file scan for one run somebody
    opened, not one per row).
    """
    from fastapi.testclient import TestClient

    from hookprobe.app import create_app
    from hookprobe.runs import RunStore
    from hookprobe.service import RunService
    from tests.helpers import FakeEngine, make_settings

    settings = make_settings(tmp_path, token="secret-token", workdir=tmp_path)
    service = RunService(settings, FakeEngine(), RunStore(tmp_path / "results"))
    auth = {"Authorization": "Bearer secret-token"}

    with TestClient(create_app(settings, service)) as client:
        client.post("/hooks/agent", json={"message": "m", "sessionKey": "probe:seen:1"}, headers=auth)
        for _ in range(300):
            if client.get("/v1/runs/probe:seen:1", headers=auth).json().get("finished"):
                break

        # The gate writes both kinds of line for this session, as it would mid-run.
        env = {
            "HOOKPROBE_GATE_AUDIT": str(tmp_path / "audit"),
            "HOOKPROBE_GATE_MODE": "readonly",
            "HOOKPROBE_GATE_WORKDIR": str(tmp_path),
            "HOOKPROBE_SESSION_KEY": "probe:seen:1",
        }
        gate.decide(
            {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "kubectl delete pod a"}},
            env,
        )
        gate.decide(
            {
                "hook_event_name": "PostToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "kubectl get secret x -o yaml"},
                "tool_response": {"stdout": "AKIAIOSFODNN7EXAMPLE"},
            },
            env,
        )

        detail = client.get("/v1/runs/probe:seen:1", headers=auth).json()
        assert detail["output_secrets"] == ["aws access key id"], "on the detail, where a person opened the run"
        listed = client.get("/v1/runs", headers=auth).json()
        rows = listed["runs"] if isinstance(listed, dict) else listed
        assert "guard_trips" in rows[0], "on the summary, so a board can show it without opening every run"
