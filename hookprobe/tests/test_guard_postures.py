"""The second posture: a runner an operator gave scoped write credentials to.

`readonly` is the investigator's and is tested next door. This file is about
`danger-only`, and about the line it draws — which is not "what may this runner
change" (its credentials answer that) but "what could it do that no credential
scope can undo".
"""

from __future__ import annotations

import pytest

from hookprobe.guard import DANGER_ONLY, MODES, READONLY, bash_deny_reason

# The work a runner with scoped credentials exists to do. Every one of these is
# refused by `readonly`, which is the whole reason the second posture exists.
THE_WORK = (
    "kubectl delete pod api-7d9f -n staging",
    "kubectl rollout restart deploy/api -n staging",
    "kubectl scale deploy/api --replicas=3 -n staging",
    "helm upgrade api ./chart -n staging",
    "git push origin fix/retry",
    "aws s3 cp report.txt s3://bucket/report.txt",
)

# What stays refused, and the reason is the same for all of them: the damage is
# to the container, the host, or an entire estate — not to the object an
# operator scoped a credential to.
BEYOND_ANY_SCOPE = (
    "rm -rf /",
    "rm -rf $EMPTY/",
    "mkfs.ext4 /dev/sda1",
    "dd if=/dev/zero of=/dev/sda",
    ":(){ :|:& };:",
    "docker run -v /:/host alpine sh",
    "terraform destroy -auto-approve",
    "kubectl delete namespace staging",
    "kubectl delete pods --all -n staging",
    "shutdown -h now",
)


@pytest.mark.parametrize("command", THE_WORK)
def test_the_work_is_refused_by_one_posture_and_allowed_by_the_other(command: str) -> None:
    assert bash_deny_reason(command, READONLY) is not None, "readonly is why a second posture was needed"
    assert bash_deny_reason(command, DANGER_ONLY) is None


@pytest.mark.parametrize("command", BEYOND_ANY_SCOPE)
def test_what_no_credential_scope_could_undo_stays_refused(command: str) -> None:
    reason = bash_deny_reason(command, DANGER_ONLY)
    assert reason is not None, f"{command!r} slipped through the danger list"
    assert "danger guard" in reason


def test_a_denial_says_which_posture_refused_it() -> None:
    """A refusal nobody can act on is a refusal that gets worked around. Each
    posture names itself, so "why did it stop" is answerable from the message."""
    assert "read-only guard" in (bash_deny_reason("kubectl delete pod x", READONLY) or "")
    assert "danger guard" in (bash_deny_reason("terraform destroy", DANGER_ONLY) or "")


def test_an_unknown_posture_fails_closed() -> None:
    """The variable behind this decides whether a runner may write. A typo in it
    must not be the thing that grants the write."""
    for mode in ("", "danger_only", "DANGER-ONLY", "off", "readonlyy", "yes"):
        assert bash_deny_reason("kubectl delete pod x", mode) is not None, f"{mode!r} granted a write"
    assert set(MODES) == {READONLY, DANGER_ONLY}, "a posture nobody can spell is a posture nobody has"


def test_the_default_posture_is_the_investigators() -> None:
    """Called with no mode at all — every existing caller, and the one that
    matters is the door that starts unattended investigations."""
    assert bash_deny_reason("kubectl delete pod x") is not None


@pytest.mark.parametrize(
    "command,why",
    [
        ("rm -rf ./build", "a scoped path is indistinguishable from / by regex; `rm -r` still works"),
        ("docker ps", "the whole binary, not its verbs — and no socket is mounted anyway"),
        ('echo "terraform destroy" > notes.txt', "the same class as a pod literally named delete"),
    ],
)
def test_the_over_blocking_is_known_rather_than_discovered(command: str, why: str) -> None:
    """This guard errs toward over-blocking by design, and the cases where it
    does are worth writing down: an agent can rephrase, and a surprise refusal
    six months from now should be findable here rather than in a transcript."""
    assert bash_deny_reason(command, DANGER_ONLY) is not None, why


def test_what_the_credential_is_supposed_to_answer_is_left_to_it() -> None:
    """The line this posture draws. Deleting a Deployment or a labelled set is
    destructive and ALLOWED — which namespace, which objects and whether at all
    are the ServiceAccount's questions, and answering them here in a regex would
    be a second, worse copy of an RBAC policy."""
    for command in (
        "kubectl delete deployment api -n staging",
        "kubectl delete pod -l app=api -n staging",
    ):
        assert bash_deny_reason(command, DANGER_ONLY) is None, command


def test_reading_is_never_refused_in_either_posture() -> None:
    for command in ("kubectl get pods -n staging", "curl -s localhost:9090/api/v1/query", "aws s3 ls s3://bucket"):
        assert bash_deny_reason(command, READONLY) is None, command
        assert bash_deny_reason(command, DANGER_ONLY) is None, command
