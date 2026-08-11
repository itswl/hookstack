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
]

DENIED = [
    "kubectl delete pod api-0 -n prod",
    "kubectl -n prod delete pod api-0",
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
]


def test_read_only_commands_pass() -> None:
    for command in ALLOWED:
        assert bash_deny_reason(command) is None, command


def test_mutating_commands_are_denied() -> None:
    for command in DENIED:
        assert bash_deny_reason(command) is not None, command
