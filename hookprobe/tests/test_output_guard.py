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

from hookprobe import gate


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
    audit = tmp_path / "audit"
    monkeypatch.setenv("HOOKPROBE_GATE_AUDIT", str(audit))
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

    lines = [json.loads(raw) for raw in (audit / next(p.name for p in audit.iterdir())).read_text().splitlines() if raw]
    assert [line.get("output_secret") for line in lines] == ["aws access key id", None]
    assert all(line["tool"] == "Bash" for line in lines)
    # The command is recorded as before — the flag is added, nothing replaced.
    assert "kubectl get secret" in lines[0]["detail"]
