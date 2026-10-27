"""The declared posture is checked against the credentials at startup.

`readonly` is a claim; the kubeconfig and the AWS identity are the facts. A
runner whose credentials can mutate while it declares readonly refuses to start
under enforce, says so under warn, and a writing posture is recorded rather than
judged. Unverifiable is its own verdict and is never mistaken for confirmed.
"""

from __future__ import annotations

import asyncio
import json
import os

import pytest

from hookprobe import posture

CAN_I_LIST_READONLY = """Resources                                       Non-Resource URLs   Resource Names   Verbs
selfsubjectaccessreviews.authorization.k8s.io   []                  []               [create]
selfsubjectrulesreviews.authorization.k8s.io    []                  []               [create]
pods                                            []                  []               [get list watch]
deployments.apps                                []                  []               [get list watch]
                                                [/healthz]          []               [get]
"""
CAN_I_LIST_ADMIN = (
    CAN_I_LIST_READONLY + "*.*                                             []                  []               [*]\n"
)
CAN_I_LIST_ONE_WRITE = (
    CAN_I_LIST_READONLY
    + "configmaps                                      []                  []               [get list update patch]\n"
)


def test_the_self_review_grants_every_subject_has_are_not_violations() -> None:
    assert posture.kube_mutating_rules(CAN_I_LIST_READONLY) == []


def test_a_wildcard_or_a_single_write_verb_is() -> None:
    assert posture.kube_mutating_rules(CAN_I_LIST_ADMIN) == ["*.*: *"]
    assert posture.kube_mutating_rules(CAN_I_LIST_ONE_WRITE) == ["configmaps: patch update"]


def test_aws_simulation_reads_allowed_actions_and_the_role_behind_a_session() -> None:
    sim = json.dumps(
        {
            "EvaluationResults": [
                {"EvalActionName": "ec2:TerminateInstances", "EvalDecision": "implicitDeny"},
                {"EvalActionName": "s3:PutObject", "EvalDecision": "allowed"},
            ]
        }
    )
    assert posture.aws_allowed(sim) == ["s3:PutObject"]
    assert posture.aws_allowed("not json") == []
    assert (
        posture.role_arn("arn:aws:sts::123456789012:assumed-role/ReadOnly/session-1")
        == "arn:aws:iam::123456789012:role/ReadOnly"
    )
    assert posture.role_arn("arn:aws:iam::123456789012:user/alice") == "arn:aws:iam::123456789012:user/alice"


def _runner(answers: dict[str, tuple[int, str, str]]):
    """A fake CLI: keyed by the first few argv words."""
    calls: list[list[str]] = []

    async def run(argv: list[str]) -> tuple[int, str, str]:
        calls.append(argv)
        for key, value in answers.items():
            if " ".join(argv).startswith(key):
                return value
        return 0, "no", ""

    run.calls = calls  # type: ignore[attr-defined]
    return run


def _with_kube(tmp_path, monkeypatch) -> None:
    cfg = tmp_path / "kube" / "config"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("apiVersion: v1\n")
    monkeypatch.setenv("KUBECONFIG", str(cfg))
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "absent"))
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "absent2"))


def test_readonly_is_confirmed_when_nothing_can_mutate(tmp_path, monkeypatch) -> None:
    _with_kube(tmp_path, monkeypatch)
    run = _runner({"kubectl auth can-i --list": (0, CAN_I_LIST_READONLY, "")})
    record = asyncio.run(posture.on_startup(tmp_path, "readonly", "enforce", run))
    assert record["verdict"] == "readonly-confirmed" and record["kube"]["mutating"] == []
    assert any(a[:4] == ["kubectl", "auth", "can-i", "delete"] for a in run.calls), (
        "the cluster-wide questions were asked"
    )
    assert posture.read(tmp_path)["verdict"] == "readonly-confirmed", "written for /v1/posture and the audit"


def test_enforce_refuses_a_runner_wider_than_it_declares(tmp_path, monkeypatch) -> None:
    _with_kube(tmp_path, monkeypatch)
    run = _runner(
        {"kubectl auth can-i --list": (0, CAN_I_LIST_READONLY, ""), "kubectl auth can-i delete pods": (0, "yes", "")}
    )
    with pytest.raises(posture.PostureViolation, match="pods"):
        asyncio.run(posture.on_startup(tmp_path, "readonly", "enforce", run))
    assert posture.read(tmp_path)["verdict"] == "wider-than-declared", (
        "recorded even though it refused — the refusal must be explainable"
    )


def test_warn_starts_and_says_so_and_danger_only_is_only_recorded(tmp_path, monkeypatch) -> None:
    _with_kube(tmp_path, monkeypatch)
    run = _runner({"kubectl auth can-i --list": (0, CAN_I_LIST_ADMIN, "")})
    assert asyncio.run(posture.on_startup(tmp_path, "readonly", "warn", run))["verdict"] == "wider-than-declared"
    assert asyncio.run(posture.on_startup(tmp_path, "danger-only", "enforce", run))["verdict"] == "recorded"


def test_unverifiable_is_a_verdict_not_a_confirmation(tmp_path, monkeypatch) -> None:
    _with_kube(tmp_path, monkeypatch)
    run = _runner({"kubectl auth can-i --list": (1, "", "Unable to connect to the server")})
    record = asyncio.run(posture.on_startup(tmp_path, "readonly", "enforce", run))
    assert record["verdict"] == "unverifiable" and record["kube"]["errors"], "it started, and did not claim readonly"


def test_no_credentials_and_off(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KUBECONFIG", str(tmp_path / "nope"))
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "absent"))
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "absent2"))
    run = _runner({})
    assert asyncio.run(posture.on_startup(tmp_path, "readonly", "enforce", run))["verdict"] == "no-credentials"
    assert run.calls == [], "nothing to ask when nothing is mounted"
    assert asyncio.run(posture.on_startup(tmp_path, "readonly", "off", run))["verdict"] == "skipped"


@pytest.mark.parametrize(
    "variables",
    [
        {"AWS_ROLE_ARN": "arn:aws:iam::123456789012:role/example", "AWS_WEB_IDENTITY_TOKEN_FILE": "/tmp/token"},
        {"AWS_CONTAINER_CREDENTIALS_RELATIVE_URI": "/v2/credentials/example"},
        {"AWS_CONTAINER_CREDENTIALS_FULL_URI": "http://localhost/credentials"},
    ],
)
def test_aws_credential_sources_are_detected(tmp_path, monkeypatch, variables) -> None:
    for name in list(os.environ):
        if name.startswith("AWS_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("HOME", str(tmp_path))
    for name, value in variables.items():
        monkeypatch.setenv(name, value)
    assert posture.aws_configured()


def _no_aws_named(tmp_path, monkeypatch) -> None:
    for name in list(os.environ):
        if name.startswith("AWS_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("HOME", str(tmp_path))


def test_a_profile_name_with_no_file_behind_it_is_not_a_credential(tmp_path, monkeypatch) -> None:
    _no_aws_named(tmp_path, monkeypatch)
    monkeypatch.setenv("AWS_PROFILE", "example")
    assert not posture.aws_configured()


def test_no_metadata_service_answering_is_no_credential(tmp_path, monkeypatch) -> None:
    """The laptop and the internal network: nothing at the address, nothing to measure."""
    _no_aws_named(tmp_path, monkeypatch)
    run = _runner({})
    aws = asyncio.run(posture.check_aws(run))
    assert aws == {"present": False} and run.calls == []
    assert posture.verdict("readonly", {"present": False}, aws) == "no-credentials"


def test_a_metadata_service_that_answers_is_measured_like_any_source(tmp_path, monkeypatch) -> None:
    _no_aws_named(tmp_path, monkeypatch)
    monkeypatch.setattr(posture, "metadata_reachable", lambda timeout=1.0: True)
    denied = [{"EvalActionName": name, "EvalDecision": "implicitDeny"} for name in posture.DANGEROUS_AWS_ACTIONS]
    run = _runner(
        {
            "aws sts": (0, '{"Arn":"arn:aws:sts::1:assumed-role/instance/i-0"}', ""),
            "aws iam": (0, json.dumps({"EvaluationResults": denied}), ""),
        }
    )
    aws = asyncio.run(posture.check_aws(run))
    assert aws["present"] is True and aws["unverifiable"] is False and aws["allowed"] == []
    assert posture.verdict("readonly", {"present": False}, aws) == "readonly-confirmed"


def test_an_instance_with_no_role_has_no_credential(tmp_path, monkeypatch) -> None:
    _no_aws_named(tmp_path, monkeypatch)
    monkeypatch.setattr(posture, "metadata_reachable", lambda timeout=1.0: True)
    run = _runner({"aws sts": (253, "", "Unable to locate credentials. You can configure credentials by running")})
    assert asyncio.run(posture.check_aws(run)) == {"present": False}


def test_metadata_disabled_is_not_asked(tmp_path, monkeypatch) -> None:
    _no_aws_named(tmp_path, monkeypatch)
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")

    def asked(timeout=1.0):
        raise AssertionError("the disabled service was asked")

    monkeypatch.setattr(posture, "metadata_reachable", asked)
    assert asyncio.run(posture.check_aws(_runner({}))) == {"present": False}


def test_the_metadata_question_goes_direct_and_counts_any_answer(monkeypatch, real_metadata_reachable) -> None:
    """Asked without the proxy: a proxy that refuses the address would otherwise
    read as a service being there."""
    import socket
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Metadata(BaseHTTPRequestHandler):
        def do_PUT(self) -> None:  # noqa: N802
            self.send_response(401)
            self.end_headers()

        def log_message(self, *args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Metadata)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    try:
        monkeypatch.setenv("AWS_EC2_METADATA_SERVICE_ENDPOINT", f"http://127.0.0.1:{server.server_port}")
        assert real_metadata_reachable(timeout=2.0) is True
    finally:
        server.shutdown()
        server.server_close()
    with socket.socket() as closed:
        closed.bind(("127.0.0.1", 0))
        port = closed.getsockname()[1]
    monkeypatch.setenv("AWS_EC2_METADATA_SERVICE_ENDPOINT", f"http://127.0.0.1:{port}")
    assert real_metadata_reachable(timeout=2.0) is False


def test_writing_node_refuses_unverifiable_declared_radius(tmp_path, monkeypatch) -> None:
    _with_kube(tmp_path, monkeypatch)
    radius = tmp_path / "radius.txt"
    radius.write_text("", encoding="utf-8")
    _with_aws_too(tmp_path, monkeypatch)
    run = _runner(
        {
            "kubectl auth can-i --list": (0, CAN_I_LIST_READONLY, ""),
            "aws sts": (0, '{"Arn":"arn:aws:iam::1:user/example"}', ""),
            "aws iam": (1, "", "AccessDenied"),
        }
    )
    with pytest.raises(posture.PostureViolation, match="could not be verified"):
        asyncio.run(posture.on_startup(tmp_path, "danger-only", "enforce", run, radius=radius))
    assert posture.read(tmp_path)["verdict"] == "unverifiable"


@pytest.mark.parametrize(
    "response",
    [
        "not-json",
        "{}",
        '{"EvaluationResults": []}',
        '{"EvaluationResults": [{"EvalActionName": "x", "EvalDecision": "bad"}]}',
    ],
)
def test_incomplete_or_invalid_aws_simulation_is_unverifiable(tmp_path, monkeypatch, response) -> None:
    _with_aws_too(tmp_path, monkeypatch)
    run = _runner(
        {
            "aws sts": (0, '{"Arn":"arn:aws:iam::1:user/example"}', ""),
            "aws iam": (0, response, ""),
        }
    )
    record = asyncio.run(posture.check_aws(run))
    assert record["unverifiable"] and record["errors"]
    assert posture.verdict("readonly", {"present": False}, record) == "unverifiable"


@pytest.mark.parametrize("malformed", [False, True])
def test_partial_simulation_still_reports_known_allowed_actions(tmp_path, monkeypatch, malformed) -> None:
    _with_aws_too(tmp_path, monkeypatch)
    results = [{"EvalActionName": "s3:PutObject", "EvalDecision": "allowed"}]
    if malformed:
        results.append(None)
    response = json.dumps({"EvaluationResults": results})
    run = _runner({"aws sts": (0, '{"Arn":"arn:aws:iam::1:user/example"}', ""), "aws iam": (0, response, "")})
    record = asyncio.run(posture.check_aws(run))
    assert record["unverifiable"] and record["allowed"] == ["s3:PutObject"]
    assert posture.verdict("readonly", {"present": False}, record) == "wider-than-declared"


def test_a_typo_in_the_mode_fails_closed(tmp_path, monkeypatch) -> None:
    _with_kube(tmp_path, monkeypatch)
    run = _runner({"kubectl auth can-i --list": (0, CAN_I_LIST_ADMIN, "")})
    with pytest.raises(posture.PostureViolation):
        asyncio.run(posture.on_startup(tmp_path, "readonly", "enforec", run))


# ── a writing node's declaration ──────────────────────────────────────────────


def _writing(mutating=(), allowed=()):
    return {"present": True, "mutating": list(mutating), "errors": []}, {
        "present": True,
        "identity": "arn:aws:iam::1:user/x",
        "allowed": list(allowed),
        "errors": [],
        "unverifiable": False,
    }


def test_an_undeclared_writing_node_is_still_only_recorded(tmp_path) -> None:
    """The behaviour a `danger-only` runner has had since it existed, kept on
    purpose. "Allowed to write" left nothing to compare against, so the check
    documented the blast radius and never judged it — and an upgrade must not
    brick a node that ran yesterday, the same rule a proposal stamped before
    the freshness cursor existed gets."""
    kube, aws = _writing(mutating=["deployments (cluster-wide): delete"])
    assert posture.verdict("danger-only", kube, aws, None) == "recorded"
    assert posture.declared_radius(None) is None
    assert posture.beyond(kube, aws, None) == []


def test_a_declared_writing_node_is_judged_against_what_it_declared(tmp_path) -> None:
    """`enforce` now means the same sentence on both postures: the credential
    may not be wider than the declaration. Under `readonly` that declaration is
    "nothing"; under `danger-only` it is a file the operator pinned."""
    kube, aws = _writing(mutating=["deployments (ns prod): restart"], allowed=["ses:PutSuppressedDestination"])
    radius = tmp_path / "radius.txt"
    radius.write_text(
        "# what this node was granted, pasted from /v1/posture\n"
        "deployments (ns prod): restart\n"
        "\n"
        "ses:PutSuppressedDestination\n",
        encoding="utf-8",
    )
    declared = posture.declared_radius(radius)
    assert declared == {"deployments (ns prod): restart", "ses:PutSuppressedDestination"}
    assert posture.verdict("danger-only", kube, aws, declared) == "within-declared-radius"

    # The credential gains one action nobody wrote down.
    kube["mutating"].append("secrets (cluster-wide): delete")
    assert posture.verdict("danger-only", kube, aws, declared) == "wider-than-declared"
    assert posture.beyond(kube, aws, declared) == ["secrets (cluster-wide): delete"]


def test_an_unreadable_declaration_is_not_the_same_as_no_declaration(tmp_path) -> None:
    """A typo in the path would otherwise hand back the unjudged posture
    silently — the operator wrote a file precisely because they wanted the
    judgement, so a missing one judges against nothing rather than nothing at
    all."""
    empty = posture.declared_radius(tmp_path / "does-not-exist.txt")
    assert empty == set(), "unreadable declares an EMPTY radius, not an absent one"
    kube, aws = _writing(allowed=["ses:PutSuppressedDestination"])
    assert posture.verdict("danger-only", kube, aws, empty) == "wider-than-declared"


def test_a_declared_writing_node_refuses_to_start_when_it_grew(tmp_path, monkeypatch) -> None:
    """The whole point: the refusal path that already existed for `readonly`
    now fires for a writing node too, and names the EXCESS rather than
    everything the node holds. Until 2026-10-06 this test ran `warn` without
    `_with_kube` — no kubeconfig, so no kubectl was ever asked and the assert
    was true either way; the enforce refusal for a writing posture had no test
    reaching it, and it is the path the operator's pinned radius leans on."""
    _with_kube(tmp_path, monkeypatch)
    radius = tmp_path / "radius.txt"
    radius.write_text("deployments (ns prod): restart\n", encoding="utf-8")

    async def fake_run(argv, **kw):
        if argv and "kubectl" in argv[0]:
            if "--list" in argv:
                return 0, CAN_I_LIST_ONE_WRITE, ""
            return 0, "no", ""
        return 1, "", "denied"

    with pytest.raises(posture.PostureViolation, match="configmaps"):
        asyncio.run(posture.on_startup(tmp_path, "danger-only", "enforce", run=fake_run, radius=radius))
    record = posture.read(tmp_path)
    assert record["verdict"] == "wider-than-declared", (
        "recorded even though it refused — the refusal must be explainable"
    )
    assert record["beyond_declared"] == ["configmaps: patch update"], "the record names the excess, not everything held"


def _with_aws_too(tmp_path, monkeypatch) -> None:
    creds = tmp_path / "aws" / "credentials"
    creds.parent.mkdir(parents=True, exist_ok=True)
    creds.write_text("[default]\n")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(creds))


def _pager():
    pages: list[str] = []

    async def alarm(text: str) -> bool:
        pages.append(text)
        return True

    return alarm, pages


def test_a_readonly_node_without_kubectl_refuses_and_pages(tmp_path, monkeypatch) -> None:
    """2026-09-29 to 10-09: the shared work image had no kubectl, both nodes
    recorded `kubectl: not found` at every boot, nothing refused, nothing paged."""
    _with_kube(tmp_path, monkeypatch)
    run = _runner({"kubectl": (127, "", "kubectl: not found")})
    alarm, pages = _pager()
    with pytest.raises(posture.PostureViolation, match="kubectl"):
        asyncio.run(posture.on_startup(tmp_path, "readonly", "enforce", run, alarm=alarm))
    record = posture.read(tmp_path)
    assert record["verdict"] == "unmeasurable" and record["kube"]["tool_missing"] == "kubectl"
    assert len([a for a in run.calls if a[0] == "kubectl"]) == 1, "one answer is the answer, not four more copies"
    assert pages and "kubectl" in pages[0], "said on the alarm channel before the refusal loops"


def test_a_writing_node_without_kubectl_starts_and_pages(tmp_path, monkeypatch) -> None:
    _with_kube(tmp_path, monkeypatch)
    run = _runner({"kubectl": (127, "", "kubectl: not found")})
    alarm, pages = _pager()
    record = asyncio.run(posture.on_startup(tmp_path, "danger-only", "enforce", run, alarm=alarm))
    assert record["verdict"] == "unmeasurable", "not `recorded`: an empty radius nobody measured is not a radius"
    assert pages and "kubectl" in pages[0]


def test_writing_node_with_declared_radius_refuses_missing_tool(tmp_path, monkeypatch) -> None:
    _with_kube(tmp_path, monkeypatch)
    radius = tmp_path / "radius.txt"
    radius.write_text("", encoding="utf-8")
    run = _runner({"kubectl": (127, "", "kubectl: not found")})
    with pytest.raises(posture.PostureViolation, match="kubectl"):
        asyncio.run(posture.on_startup(tmp_path, "danger-only", "enforce", run, radius=radius))
    assert posture.read(tmp_path)["verdict"] == "unmeasurable"


def test_warn_starts_a_readonly_node_without_its_tool_and_pages(tmp_path, monkeypatch) -> None:
    _with_kube(tmp_path, monkeypatch)
    run = _runner({"kubectl": (127, "", "kubectl: not found")})
    alarm, pages = _pager()
    record = asyncio.run(posture.on_startup(tmp_path, "readonly", "warn", run, alarm=alarm))
    assert record["verdict"] == "unmeasurable" and pages


def test_a_missing_aws_cli_is_named_and_a_measured_violation_outranks_it(tmp_path, monkeypatch) -> None:
    _with_kube(tmp_path, monkeypatch)
    _with_aws_too(tmp_path, monkeypatch)
    run = _runner({"kubectl auth can-i --list": (0, CAN_I_LIST_READONLY, ""), "aws": (127, "", "aws: not found")})
    with pytest.raises(posture.PostureViolation, match="aws"):
        asyncio.run(posture.on_startup(tmp_path, "readonly", "enforce", run))
    assert posture.read(tmp_path)["aws"]["tool_missing"] == "aws"
    run = _runner({"kubectl auth can-i --list": (0, CAN_I_LIST_ADMIN, ""), "aws": (127, "", "aws: not found")})
    with pytest.raises(posture.PostureViolation, match=r"\*"):
        asyncio.run(posture.on_startup(tmp_path, "readonly", "enforce", run))
    assert posture.read(tmp_path)["verdict"] == "wider-than-declared", "what was measured outranks what could not be"
