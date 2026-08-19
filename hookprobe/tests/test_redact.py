"""Credential-shaped material never reaches anything durable.

The leak this closes was not "a secret in a log". A tool call's detail line is
copied to the run's event feed, to `results/*.json`, to the flight recorder's
`audit/*.jsonl`, and — through distill — into the case block of a generated
SKILL.md that every later run loads as standing instruction and /v1/skills
serves to a browser. So one bearer token in one command propagated into the
investigator's own learned knowledge, where nobody would think to look for it.
That is why the masking sits at the capture point and why this file checks the
far end of the pipe, not just the function.
"""

from __future__ import annotations

from hookprobe.engine import _tool_detail
from hookprobe.redact import redact


def test_the_shapes_that_carry_a_secret_are_masked() -> None:
    """Masked, not deleted: an investigator reading its own case file still has
    to see that a header WAS sent and which host was reached."""
    cases = [
        ('curl -H "Authorization: Bearer sk-ant-abc123def456" https://api.example/v1', "sk-ant-abc123def456"),
        ("psql postgres://admin:s3cr3tpw@db-1:5432/orders", "s3cr3tpw"),
        ("mysql -h db-1 -u root -pMyPa55word -e 'show processlist'", "MyPa55word"),
        ("kubectl get pods --token=eyJhbGciOiJSUzI1NiJ9.abc", "eyJhbGciOiJSUzI1NiJ9.abc"),
        ("redis-cli -a hunter2 --scan", "hunter2"),
        ("aws s3 ls --secret-access-key wJalrXUtnFEMI0K7MDENGbPxRfiCY", "wJalrXUtnFEMI0K7MDENGbPxRfiCY"),
    ]
    for command, secret in cases:
        masked = _tool_detail({"command": command})
        assert secret not in masked, f"the secret survived: {masked}"
        assert "[redacted]" in masked, f"nothing was masked at all: {masked}"
        # The shape survives — the host and the flag are the evidence.
        assert masked.split()[0] == command.split()[0]


def test_it_does_not_eat_the_evidence() -> None:
    """A redaction that mangles ordinary commands costs real diagnostic value to
    protect nothing. These carry no credential and must come through intact."""
    intact = [
        "kubectl get pods -n prod -o wide",
        "git log --author=alice --oneline",
        "kubectl logs deploy/api --since=15m",
        "dig +short api.internal",
        "ps aux | grep -c uvicorn",
    ]
    for command in intact:
        assert _tool_detail({"command": command}) == command


def test_a_secret_cannot_reach_a_distilled_skill() -> None:
    """The propagation path is the reason this exists: distill embeds tool
    details verbatim into a SKILL.md that later runs load. Whatever the masking
    does or does not catch, what it DID catch must not reappear here."""
    from hookprobe import distill

    leaky = _tool_detail({"command": 'curl -H "Authorization: Bearer sk-live-DEADBEEF" https://api.example'})
    # The real shape distill reads: turns, each holding tool_use events whose
    # `detail` is exactly what _tool_detail produced.
    turns = [{"events": [{"type": "tool_use", "name": "Bash", "detail": leaky}]}]
    rendered = "\n".join(distill._steps(turns))
    assert "sk-live-DEADBEEF" not in rendered
    assert "[redacted]" in rendered


def test_redact_is_idempotent() -> None:
    """The same string passes through more than one copy on its way to disk, and
    a marker that gets re-masked would turn into nonsense."""
    once = redact('curl -H "Authorization: Bearer sk-abc123" https://x')
    assert redact(once) == once
