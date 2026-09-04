"""The bash guard's verb blocklist, from both sides.

Second of the three read-only layers — the read-only credentials are the real
boundary and the container is the third. What is asserted here is the guard's
own claim: the mutating verbs of kubectl, helm, systemctl and terraform, plus
ssh/scp, are denied before the tool runs.
"""

from __future__ import annotations

from hookprobe.guard import bash_deny_reason

ALLOWED = [
    "kubectl get pods -n prod",
    "kubectl describe pod api-0 -n prod",
    "kubectl logs deploy/api -n prod --since=1h",
    "kubectl top nodes",
    "kubectl get events -n prod --sort-by=.lastTimestamp",
    "helm list -A",
    "helm status api -n prod",
    "helm get values api -n prod",
    "docker ps",
    "docker inspect api",
    "docker logs api --tail 100",
    "systemctl status nginx",
    "curl -s 'http://prometheus:9090/api/v1/query?query=up'",
    # Query APIs legitimately POST; HTTP verbs are deliberately not policed.
    "curl -s -X POST http://loki:3100/loki/api/v1/query_range -d 'query={app=\"api\"}'",
    "python3 analyze.py",
    "rm -rf ./scratch && mkdir ./scratch",
    "git log --oneline -5",
    "git diff HEAD~1",
    # AWS reads. The rule for this CLI is INVERTED — deny unless the command
    # reads — so both directions need pinning or the inversion rots quietly.
    "aws s3 ls s3://bucket",
    "aws s3api get-object --bucket b --key k /tmp/o",
    "aws ec2 describe-instances",
    "aws logs describe-log-groups",
    "aws iam list-users",
    "aws sts get-caller-identity",
    "aws cloudwatch get-metric-statistics --namespace AWS/EC2",
    # Global flags before the service. Positional parsing denied these two,
    # because the regex could backtrack into reading "eu-west-1" as the service.
    "aws --region eu-west-1 ec2 describe-volumes",
    "aws --profile viewer --output json rds describe-db-instances",
    "aws ec2 describe-instances | jq .",
    "aws s3 ls && aws s3api list-buckets",
]

DENIED = [
    "kubectl delete pod api-0 -n prod",
    "kubectl -n prod delete pod api-0",
    # A shell line-continuation used to split the verb into a second segment and
    # slip past — the way a model wraps a long line, not only an attacker.
    "kubectl \\\n  delete pod api-0 -n prod",
    "kubectl -n prod \\\n  rollout restart deploy/api",
    "helm \\\n  upgrade api ./chart",
    # Alternate binaries for the same clusters.
    "oc delete pod api-0 -n prod",
    "kubecolor delete pod api-0",
    "helmfile apply",
    "helmfile destroy",
    "kubectl apply -f fix.yaml",
    "kubectl rollout restart deploy/api",
    "kubectl exec -it api-0 -- sh",
    "kubectl get pods && kubectl scale deploy/api --replicas=0",
    "kubectl port-forward svc/db 5432:5432",
    "helm upgrade api ./chart",
    "helm uninstall api",
    "docker restart api",
    "docker exec -it api sh",
    "systemctl restart nginx",
    "service nginx restart",
    "terraform apply -auto-approve",
    "ansible-playbook site.yml",
    "ssh node-1 uptime",
    "scp node-1:/var/log/app.log .",
    "reboot",
    "git push origin main",
    # AWS writes. Enumerating this CLI's mutating verbs was rejected: the API
    # gains operations weekly, so a denylist against it is wrong by next
    # quarter, and wrong in that direction means an agent holding real keys.
    "aws s3 rm s3://bucket/key",
    "aws s3 cp ./local s3://b/k",
    "aws s3 sync . s3://b",
    "aws s3api put-object --bucket b --key k",
    "aws ec2 terminate-instances --instance-ids i-1",
    "aws ec2 run-instances --image-id ami-1",
    "aws ec2 delete-volume --volume-id v-1",
    "aws iam create-user --user-name x",
    "aws iam put-user-policy --user-name x --policy-name p",
    "aws lambda invoke --function-name f out.json",
    "aws --region eu-west-1 s3 rm s3://b/k",
    # A read verb inside a PATH must not vouch for the command around it.
    "aws s3 rm s3://b/list-of-keys",
    # One read does not license the write chained after it.
    "aws ec2 describe-instances && aws ec2 terminate-instances --instance-ids i-1",
]


def test_read_only_commands_pass() -> None:
    for command in ALLOWED:
        assert bash_deny_reason(command) is None, command


def test_mutating_commands_are_denied() -> None:
    for command in DENIED:
        assert bash_deny_reason(command) is not None, command


def test_the_verbs_that_do_the_same_thing_as_a_blocked_one() -> None:
    """Each of these was missing while its twin was blocked, which is the worst
    shape for a denylist to be in: `systemctl start` refused and `systemctl kill`
    allowed reads as a considered decision when it was an omission."""
    for command in (
        "kubectl run nginx --image=nginx",  # starts a workload; `create` was blocked
        "docker start api",  # `stop` and `restart` were blocked
        "docker unpause api",
        "docker cp ./x api:/tmp/x",  # writes into a running container
        "systemctl kill nginx",  # `stop` was blocked
        "systemctl unmask nginx",  # `mask` was blocked
    ):
        assert bash_deny_reason(command), f"still allowed: {command}"


def test_the_evasions_this_guard_does_not_claim_to_stop() -> None:
    """Pinned as KNOWN, not as acceptable-in-general.

    Each rule needs a binary and a verb in one pipeline segment, so separating
    them gets through. None of it is closable without parsing the shell, and the
    module docstring now says so rather than claiming to catch every chained
    segment. What holds against an adversary is the read-only credential; this
    layer is for an over-eager model.

    The test exists so that if someone ever DOES close one of these, they find
    out here and can delete the line — and so nobody rediscovers them believing
    the guard was supposed to have covered them.
    """
    known_gaps = (
        "echo delete | xargs kubectl",  # binary and verb in different segments
        "V=delete; kubectl $V pod x",  # the verb is a variable at match time
        'kubectl "de""lete" pod x',  # the verb is assembled by the shell
    )
    for command in known_gaps:
        assert bash_deny_reason(command) is None, (
            f"{command!r} is now blocked — good; remove it from known_gaps and from the guard's docstring"
        )
