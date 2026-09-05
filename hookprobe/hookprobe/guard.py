"""Guard for the agent's Bash tool, in one of two postures.

The real security boundary is the credentials mounted into the container:
give the runner a read-only kubeconfig and query-only tokens, and nothing
here matters. This guard is defense in depth — it blocks the common mutating
verbs of the CLIs an SRE agent reaches for, so an over-eager model gets a
clear denial instead of a live-fire mistake. It deliberately errs toward
over-blocking (a pod literally named "delete" will trip it): the agent can
rephrase a query, the cluster cannot un-apply a change.

HTTP verbs are deliberately NOT policed — evidence collection legitimately
POSTs to Loki/Elasticsearch query endpoints. Cloud-vendor CLIs are too many
to enumerate; scope their credentials instead.

TWO POSTURES, because this family now has two kinds of runner.

  readonly     (default) everything above. The investigator's posture, and the
               only one that should ever face an event door.
  danger-only  the mutating verbs are ALLOWED and a much shorter list is not:
               the handful that destroy a machine, a filesystem or a whole
               estate rather than one object in it.

WHY NOT AN ALLOWLIST, since a closed set is this repository's usual answer and
is what `HOOKPROBE_MCP_TOOLS` does. Because the two are answering different
questions. An MCP allowlist bounds a KNOWN, enumerable set of tools an operator
picked. A runner in `danger-only` exists to do work its operator delegated
without enumerating it in advance — that is the whole reason it is an agent and
not the executor in remediation.py, which DOES take an allowlist because there
the operator approved specific argv. An allowlist here would be permanently
incomplete, and a tool that stops half the time is a tool somebody goes back to
doing by hand.

So `danger-only` is honest about what it is: not a sandbox, and not pretending
to bound what the runner may do. What bounds that is the credential mounted in.
This list only removes the accidents that no credential scope can undo — the
ones where the blast radius is the container or the host rather than the thing
the operator scoped.
"""

from __future__ import annotations

import re

# Segment-scoped: each pattern stops at a pipeline separator, so a mutating verb
# in the SECOND half of `kubectl get pods; kubectl delete pod x` is still caught
# — one re.search over the whole string finds it in its own segment.
#
# WHAT THIS DOES NOT CATCH, stated because the comment here used to claim it
# caught "every chained segment" and that reads as more than it is. Each rule
# needs a binary and a verb in ONE segment, so an adversary who separates them
# gets through, and three ways of doing that are known and verified:
#
#   echo delete | xargs kubectl      — binary and verb in different segments
#   V=delete; kubectl $V pod x       — the verb is a variable at match time
#   kubectl "de""lete" pod x         — the verb is assembled by the shell
#
# None of these is closable without parsing the shell, and a regex that tried
# would either miss the next trick or refuse ordinary queries. That is fine, and
# saying so is the point: this layer is defence in depth for an over-eager MODEL,
# not a sandbox against an adversary. The boundary that holds against one is the
# read-only credential mounted into the container — and for the remediation path,
# the allowlist plus exec-without-a-shell (see hookprobe/remediation.py).
_SEG = r"[^|;&\n]*"
# An AWS operation that only reads, by the API's own naming convention. The
# lookbehind keeps a path from counting as one: `aws s3 rm s3://b/list-of-keys`
# contains "list" and must still be denied.
#
# Positional parsing was tried first and abandoned — `aws --region eu-west-1 ec2
# describe-volumes` was DENIED because the regex could backtrack into reading
# "eu-west-1" as the service and "ec2" as the operation. Asking "does this
# command read anything at all" needs no parse and has no such seam.
# `update-kubeconfig` is the one exception that is not a read VERB and is still
# a read: it calls DescribeCluster and writes a file on this machine, and can
# mutate nothing in AWS. Denying it was the first false positive this inverted
# rule produced — an investigator that may query EKS could not reach the
# cluster's API at all. Named individually rather than by loosening the verb
# list, because "update" staying denied everywhere else is the point.
_AWS_READ = (
    r"(?<![/:.\w-])(?:describe|get|list|search|head|scan|query|select|lookup|"
    r"estimate|preview|validate|check|test|ls|update-kubeconfig)[a-z0-9-]*\b"
)

_DENY_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            rf"\b(?:kubectl|oc|kubecolor)\b{_SEG}\b(apply|create|delete|edit|patch|replace|scale|autoscale|rollout|run|"
            rf"cordon|uncordon|drain|taint|label|annotate|expose|set|exec|attach|cp|port-forward|proxy|debug)\b"
        ),
        "kubectl mutation or pod entry",
    ),
    (
        re.compile(
            rf"\b(?:helm|helmfile)\b{_SEG}\b(install|upgrade|uninstall|delete|rollback|push|apply|sync|destroy)\b"
        ),
        "helm mutation",
    ),
    (
        re.compile(
            rf"\b(docker|podman|nerdctl|crictl)\b{_SEG}\b(run|exec|rm|rmi|stop|start|kill|restart|unpause|cp|build|push|prune|create)\b"
        ),
        "container runtime mutation",
    ),
    (
        re.compile(rf"\bsystemctl\b{_SEG}\b(start|stop|restart|reload|enable|disable|mask|unmask|kill|isolate)\b"),
        "service state change",
    ),
    (
        re.compile(r"\bservice\s+\S+\s+(start|stop|restart|reload)\b"),
        "service state change",
    ),
    (
        re.compile(rf"\b(terraform|tofu)\b{_SEG}\b(apply|destroy)\b"),
        "infrastructure mutation",
    ),
    (re.compile(r"\bansible-playbook\b"), "configuration management run"),
    (re.compile(r"\b(ssh|scp|sftp|rsync)\b"), "remote shell / file transfer"),
    (re.compile(r"\b(shutdown|reboot|poweroff|halt)\b"), "host power control"),
    (re.compile(rf"\bgit\b{_SEG}\bpush\b"), "git push"),
    # AWS is the one CLI here whose surface is too large and too fast-moving to
    # enumerate mutating verbs for, so this inverts: an operation is denied
    # unless it reads. Every other rule above is "deny these verbs"; this is
    # "deny anything that is not one of these", because a denylist against an
    # API that gains operations weekly is a list that is wrong by next quarter,
    # and being wrong in that direction means an agent holding somebody's keys.
    #
    # `aws s3 ls` and `aws s3api get-object` pass; `aws s3 cp|mv|rm|sync` and
    # every `put-`/`create-`/`delete-`/`terminate-` do not. The read verbs are
    # the AWS API's own naming convention, which is the only reason a list this
    # short can cover it.
    (
        re.compile(rf"\baws\b(?![^|;&\n]*{_AWS_READ})"),
        "aws non-read operation",
    ),
)


# The `danger-only` list. Deliberately short, and every entry is something whose
# damage is NOT bounded by the credentials an operator scoped: a wiped
# filesystem, a reformatted disk, a fork bomb, a container escape hatch, an
# estate torn down in one verb. `kubectl delete pod x` is absent on purpose —
# that is the work, and the ServiceAccount is what says which pods.
_DANGER_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    # `rm -rf /`, `rm -fr /usr`, and the same with the root reached through a
    # variable that is empty at match time (`rm -rf $D/`, the classic).
    (re.compile(r"\brm\b[^|;&\n]*\s-[a-zA-Z]*[rR][a-zA-Z]*f|\brm\b[^|;&\n]*\s-[a-zA-Z]*f[a-zA-Z]*[rR]"), "rm -rf"),
    (re.compile(r"\b(mkfs|mkfs\.\w+|fdisk|parted|wipefs|shred)\b"), "a filesystem or partition command"),
    (re.compile(r"\bdd\b[^|;&\n]*\bof=/dev/"), "dd onto a device"),
    (re.compile(r":\(\)\s*\{.*\|.*&.*\}\s*;?\s*:"), "a fork bomb"),
    # The container's own escape hatches. There is no socket mounted, so these
    # cannot work today; the rule is what keeps that true if one ever is.
    (re.compile(r"\b(docker|podman|nerdctl|containerd|runc)\b"), "a container runtime"),
    (re.compile(r"\bterraform\b[^|;&\n]*\bdestroy\b|\bpulumi\b[^|;&\n]*\bdestroy\b"), "an infrastructure teardown"),
    # Whole-namespace and whole-cluster deletes: the credential scopes WHICH
    # namespace, never "all of it at once".
    (re.compile(r"\bkubectl\b[^|;&\n]*\bdelete\b[^|;&\n]*(--all\b|\bnamespace\b|\bns\b)"), "a namespace-wide delete"),
    (re.compile(r"\b(shutdown|reboot|halt|poweroff)\b"), "a host power command"),
)

READONLY, DANGER_ONLY = "readonly", "danger-only"
MODES = (READONLY, DANGER_ONLY)


def bash_deny_reason(command: str, mode: str = READONLY) -> str | None:
    """Return a human-readable denial reason, or None when the command may run.

    An unknown mode falls back to `readonly`. Failing closed is the only safe
    direction for a typo in an env var that decides whether a runner may write.
    """
    # A shell line-continuation (backslash then newline) joins two physical lines
    # into ONE logical command, but _SEG stops at \n — so `kubectl \<newline>
    # delete pod x` split the verb into a second segment and passed. Models wrap
    # long kubectl/helm lines exactly this way, so this was an accidental hole,
    # not only an adversarial one. Collapse continuations before matching.
    command = re.sub(r"\\\r?\n", " ", command)
    if mode == DANGER_ONLY:
        for pattern, label in _DANGER_RULES:
            if pattern.search(command):
                return (
                    f"danger guard: {label} is blocked. This runner may change what its "
                    f"credentials reach; it may not do something no credential scope can undo"
                )
        return None
    for pattern, label in _DENY_RULES:
        if pattern.search(command):
            return f"read-only guard: {label} is blocked; this runner may only observe, never change"
    return None
