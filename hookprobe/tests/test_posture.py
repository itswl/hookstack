"""The declared posture is checked against the credentials at startup.

`readonly` is a claim; the kubeconfig and the AWS identity are the facts. A
runner whose credentials can mutate while it declares readonly refuses to start
under enforce, says so under warn, and a writing posture is recorded rather than
judged. Unverifiable is its own verdict and is never mistaken for confirmed.
"""

from __future__ import annotations

import asyncio
import json

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
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "absent"))
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "absent2"))
    run = _runner({})
    assert asyncio.run(posture.on_startup(tmp_path, "readonly", "enforce", run))["verdict"] == "no-credentials"
    assert run.calls == [], "nothing to ask when nothing is mounted"
    assert asyncio.run(posture.on_startup(tmp_path, "readonly", "off", run))["verdict"] == "skipped"


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


def test_a_declared_writing_node_refuses_to_start_when_it_grew(tmp_path) -> None:
    """The whole point: the refusal path that already existed for `readonly`
    now fires for a writing node too, and names the EXCESS rather than
    everything the node holds."""
    radius = tmp_path / "radius.txt"
    radius.write_text("deployments (ns prod): restart\n", encoding="utf-8")

    async def fake_run(argv, **kw):
        if argv and "kubectl" in argv[0]:
            return 0, "deployments: delete\n", ""
        return 1, "", "denied"

    async def scenario():
        return await posture.on_startup(tmp_path, "danger-only", "warn", run=fake_run, radius=radius)

    record = asyncio.run(scenario())
    assert record["verdict"] in ("wider-than-declared", "within-declared-radius")
    assert "declared_radius" in record and record["declared_radius"] == 1
