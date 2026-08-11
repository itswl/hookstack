"""Read-only guard for the agent's Bash tool.

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
"""

from __future__ import annotations

import re

# Segment-scoped: each pattern stops at a pipeline separator, so one scan of
# the full command string still catches every chained segment via re.search.
_SEG = r"[^|;&\n]*"

_DENY_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            rf"\bkubectl\b{_SEG}\b(apply|create|delete|edit|patch|replace|scale|autoscale|rollout|"
            rf"cordon|uncordon|drain|taint|label|annotate|expose|set|exec|attach|cp|port-forward|proxy|debug)\b"
        ),
        "kubectl mutation or pod entry",
    ),
    (
        re.compile(rf"\bhelm\b{_SEG}\b(install|upgrade|uninstall|delete|rollback|push)\b"),
        "helm mutation",
    ),
    (
        re.compile(
            rf"\b(docker|podman|nerdctl|crictl)\b{_SEG}\b(run|exec|rm|rmi|stop|kill|restart|build|push|prune|create)\b"
        ),
        "container runtime mutation",
    ),
    (
        re.compile(rf"\bsystemctl\b{_SEG}\b(start|stop|restart|reload|enable|disable|mask|isolate)\b"),
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
)


def bash_deny_reason(command: str) -> str | None:
    """Return a human-readable denial reason, or None when the command may run."""
    for pattern, label in _DENY_RULES:
        if pattern.search(command):
            return f"read-only guard: {label} is blocked; this runner may only observe, never change"
    return None
