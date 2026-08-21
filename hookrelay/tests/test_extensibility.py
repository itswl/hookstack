"""The three sockets: custom adapter, custom processor pipeline, custom channel.

These tests exercise the same paths a plugin author walks, including loading
the SHIPPED examples from examples/plugins — examples that are not tested rot
into lies.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import pytest

from hookrelay import registry
from hookrelay.channels import build_request
from hookrelay.config import Channel, Config, ConfigError
from hookrelay.pipeline import handle_hook

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "plugins"


@pytest.fixture(scope="module", autouse=True)
def load_example_plugins():
    """Load the shipped examples once; tolerate re-registration across runs."""
    if "github" not in registry.SOURCE_ADAPTERS or "aws-sns" not in registry.SOURCE_ADAPTERS:
        registry.load_plugins(EXAMPLES)


def _github_cfg() -> Config:
    return Config.from_dict(
        {
            "sources": [
                {
                    "name": "gh",
                    "adapter": "github",
                    "secret": "gh-secret",
                    "title": "{repository.full_name}",
                    "body": "{head_commit.message}",
                }
            ],
            "channels": [{"name": "sink", "type": "generic", "url": "https://sink.example/in"}],
            "routes": [{"name": "all", "source": "*", "send_to": ["sink"]}],
        }
    )


def test_github_adapter_speaks_the_github_dialect():
    adapter = registry.SOURCE_ADAPTERS["github"]
    source = _github_cfg().sources["gh"]
    body = json.dumps({"repository": {"full_name": "a/b"}}).encode()
    good = "sha256=" + hmac.new(b"gh-secret", body, hashlib.sha256).hexdigest()

    assert adapter.verify(source, body, {"x-hub-signature-256": good})
    assert not adapter.verify(source, body, {"x-hub-signature-256": "sha256=" + "0" * 64})
    assert not adapter.verify(source, body, {"x-hook-signature": good}), "wrong header must not count"

    extracted = adapter.parse(source, {"repository": {"full_name": "a/b"}, "head_commit": {"message": "fix"}})
    assert extracted["title"] == "a/b" and extracted["body"] == "fix"


def _sns_ns() -> dict:
    """The plugin module's namespace, reached through its registered class —
    load_plugins does not put plugin modules on sys.modules."""
    return type(registry.SOURCE_ADAPTERS["aws-sns"]).parse.__globals__


def _sns_cfg() -> Config:
    return Config.from_dict(
        {
            "sources": [
                {
                    "name": "cloudwatch",
                    "adapter": "aws-sns",
                    "secret": "",
                    "title": "{AlarmName}",
                    "body": "{NewStateReason}",
                    "level": "{NewStateValue}",
                    "level_map": {"ALARM": "high", "OK": "info"},
                }
            ],
            "channels": [{"name": "sink", "type": "generic", "url": "https://sink.example/in"}],
            "routes": [{"name": "all", "source": "*", "send_to": ["sink"]}],
        }
    )


def test_sns_notification_unwraps_the_message_string(monkeypatch):
    """The whole reason the adapter exists: templates reach INSIDE Message."""
    monkeypatch.setitem(_sns_ns(), "_verify_pki", lambda payload: True)
    adapter = registry.SOURCE_ADAPTERS["aws-sns"]
    source = _sns_cfg().sources["cloudwatch"]
    alarm = {"AlarmName": "HighCPU", "NewStateValue": "ALARM", "NewStateReason": "cpu 97% for 5m"}
    payload = {"Type": "Notification", "Message": json.dumps(alarm), "TopicArn": "arn:aws:sns:x:1:t"}

    extracted = adapter.parse(source, payload)
    assert extracted["title"] == "HighCPU"
    assert extracted["body"] == "cpu 97% for 5m"
    assert extracted["level"] == "high"


def test_sns_handshake_is_pinned_to_sns_hosts(monkeypatch):
    from fastapi import HTTPException

    ns = _sns_ns()
    monkeypatch.setitem(ns, "_verify_pki", lambda payload: True)
    adapter = registry.SOURCE_ADAPTERS["aws-sns"]
    source = _sns_cfg().sources["cloudwatch"]

    assert ns["_url_is_sns"]("https://sns.us-east-1.amazonaws.com/confirm?x=1")
    assert not ns["_url_is_sns"]("http://sns.us-east-1.amazonaws.com/confirm")  # not https
    assert not ns["_url_is_sns"]("https://evil.example/confirm")

    with pytest.raises(HTTPException) as refused:
        adapter.parse(source, {"Type": "SubscriptionConfirmation", "SubscribeURL": "https://evil.example/c"})
    assert refused.value.status_code == 400

    fetched: list[str] = []
    monkeypatch.setitem(ns, "_fetch", lambda url: fetched.append(url) or b"ok")
    with pytest.raises(HTTPException) as confirmed:
        adapter.parse(
            source,
            {"Type": "SubscriptionConfirmation", "SubscribeURL": "https://sns.us-east-1.amazonaws.com/c"},
        )
    assert confirmed.value.status_code == 202
    assert fetched == ["https://sns.us-east-1.amazonaws.com/c"]


def test_sns_pki_signature_roundtrip():
    """Real verification against a self-signed cert — no network, no mocks."""
    cryptography = pytest.importorskip("cryptography")  # noqa: F841
    import datetime

    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    from cryptography.x509 import CertificateBuilder, Name, NameAttribute, random_serial_number
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = Name([NameAttribute(NameOID.COMMON_NAME, "sns.amazonaws.com")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )

    ns = _sns_ns()
    cert_url = "https://sns.us-east-1.amazonaws.com/test-cert.pem"
    ns["_CERT_CACHE"][cert_url] = cert.public_bytes(serialization.Encoding.PEM)

    payload = {
        "Type": "Notification",
        "Message": '{"AlarmName":"X"}',
        "MessageId": "m-1",
        "Timestamp": "2026-08-12T00:00:00Z",
        "TopicArn": "arn:aws:sns:x:1:t",
        "SignatureVersion": "1",
        "SigningCertURL": cert_url,
    }
    fields = ns["_SIGNED_FIELDS"]["Notification"]
    string_to_sign = "".join(f"{k}\n{payload[k]}\n" for k in fields if payload.get(k) is not None)
    import base64

    payload["Signature"] = base64.b64encode(
        key.sign(string_to_sign.encode(), padding.PKCS1v15(), hashes.SHA1())
    ).decode()

    assert ns["_verify_pki"](payload) is True
    tampered = dict(payload, Message='{"AlarmName":"Y"}')
    assert ns["_verify_pki"](tampered) is False


def test_custom_channel_type_from_example_plugin():
    channel = Channel(name="log", type="oneline", url="https://collector.example/append")
    url, payload, _headers = build_request(
        channel,
        {"event_id": 3, "source": "gh", "title": "a/b", "body": "", "level": "info", "fields": {}},
        now=0.0,
    )
    assert url == "https://collector.example/append"
    assert json.loads(payload.decode())["line"] == "[info] gh: a/b"


def test_unknown_names_fail_at_boot_not_first_event():
    with pytest.raises(ConfigError, match="unknown adapter"):
        Config.from_dict(
            {
                "sources": [{"name": "x", "adapter": "nope"}],
                "channels": [{"name": "c", "type": "generic", "url": "http://x"}],
                "routes": [{"name": "r", "send_to": ["c"]}],
            }
        )
    with pytest.raises(ConfigError, match="unknown processor"):
        Config.from_dict(
            {
                "sources": [{"name": "x"}],
                "channels": [{"name": "c", "type": "generic", "url": "http://x"}],
                "routes": [{"name": "r", "send_to": ["c"]}],
                "pipeline": ["dedup", "sorcery", "routes"],
            }
        )
    with pytest.raises(ConfigError, match="no 'routes' stage"):
        Config.from_dict(
            {
                "sources": [{"name": "x"}],
                "channels": [{"name": "c", "type": "generic", "url": "http://x"}],
                "routes": [{"name": "r", "send_to": ["c"]}],
                "pipeline": ["dedup", "silence"],
            }
        )


def _pipeline_cfg(pipeline: list) -> Config:
    return Config.from_dict(
        {
            "sources": [{"name": "ci", "secret": "", "title": "{job}", "body": "{detail}", "level": "{status}"}],
            "channels": [
                {"name": "loud", "type": "generic", "url": "https://loud.example"},
                {"name": "quiet", "type": "generic", "url": "https://quiet.example"},
            ],
            "routes": [
                {"name": "high-loud", "source": "*", "when": {"level": ["high"]}, "send_to": ["loud"], "priority": 10},
                {"name": "rest-quiet", "source": "*", "send_to": ["quiet"], "priority": 0},
            ],
            "pipeline": pipeline,
        }
    )


async def test_set_stage_changes_routing_outcome(store):
    cfg = _pipeline_cfg(
        ["dedup", "silence", {"type": "set", "name": "escalate-ci", "set": {"level": "high"}}, "routes"]
    )
    result = await handle_hook(store, cfg, cfg.sources["ci"], {"job": "build", "status": "meh"}, now=1000.0)
    assert result["channels"] == ["loud", "quiet"]
    assert any(step.get("gate") == "escalate-ci" and step.get("result") == "applied" for step in result["steps"])


async def test_filter_stage_drops_with_named_code(store):
    cfg = _pipeline_cfg(
        [
            "dedup",
            {"type": "filter", "name": "mute-low", "when": {"level": ["low"]}, "skip_code": "low_muted"},
            "routes",
        ]
    )
    result = await handle_hook(
        store, cfg, cfg.sources["ci"], {"job": "build", "status": "low", "detail": "x"}, now=1000.0
    )
    assert result["outcome"] == "skipped" and result["skip_code"] == "low_muted"


class _StubResponse:
    def __init__(self, status_code: int, data):
        self.status_code = status_code
        self._data = data
        self.text = json.dumps(data)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self):
        return self._data


class _StubClient:
    """Stands in for httpx.AsyncClient inside the http processor."""

    def __init__(self, response: _StubResponse | Exception):
        self._response = response
        self.requests: list[dict] = []

    async def post(self, url, json=None, headers=None, timeout=None):
        self.requests.append({"url": url, "json": json, "headers": headers})
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


async def test_http_processor_applies_external_verdict(store):
    """The platform-shaped integration: an external brain rewrites the
    level and the rewritten event routes accordingly."""
    cfg = _pipeline_cfg(
        [
            "dedup",
            {
                "type": "http",
                "name": "brain",
                "url": "https://brain.example/triage",
                "headers": {"authorization": "Bearer k"},
            },
            "routes",
        ]
    )
    client = _StubClient(
        _StubResponse(200, {"action": "pass", "set": {"level": "high", "fields": {"scored_by": "brain"}}})
    )
    result = await handle_hook(
        store, cfg, cfg.sources["ci"], {"job": "deploy", "status": "meh"}, now=1000.0, client=client
    )

    assert result["channels"] == ["loud", "quiet"]
    sent = client.requests[0]
    assert sent["json"]["event"]["title"] == "deploy" and sent["headers"]["authorization"] == "Bearer k"
    recent = await store.recent_events(1)
    assert recent[0]["level"] == "high"


async def test_http_processor_can_drop(store):
    cfg = _pipeline_cfg(["dedup", {"type": "http", "name": "brain", "url": "https://b.example"}, "routes"])
    client = _StubClient(_StubResponse(200, {"action": "drop", "skip_code": "brain_said_no"}))
    result = await handle_hook(store, cfg, cfg.sources["ci"], {"job": "x"}, now=1000.0, client=client)
    assert result["outcome"] == "skipped" and result["skip_code"] == "brain_said_no"


async def test_http_processor_error_policies(store):
    base = ["dedup", {"type": "http", "name": "brain", "url": "https://b.example", "on_error": "pass"}, "routes"]
    cfg = _pipeline_cfg(base)
    client = _StubClient(TimeoutError("no answer"))
    result = await handle_hook(store, cfg, cfg.sources["ci"], {"job": "x"}, now=1000.0, client=client)
    assert result["outcome"] == "routed", "fail-open must let the event through"
    assert any(step.get("result") == "error_pass" for step in result["steps"])

    strict = _pipeline_cfg(
        ["dedup", {"type": "http", "name": "brain", "url": "https://b.example", "on_error": "drop"}, "routes"]
    )
    result = await handle_hook(store, strict, strict.sources["ci"], {"job": "y"}, now=2000.0, client=client)
    assert result["skip_code"] == "processor_error", "fail-closed must drop with the named code"


async def test_a_dry_run_reports_the_brain_call_instead_of_making_it(store):
    """/explain promises an answer that cannot leave this process, and its
    docstring said so — while the http stage POSTed the payload to the
    configured brain on the way past. A dry run was handing a real payload to a
    real external service (and, for a per-call brain, a real bill) to answer a
    question about a payload nobody sent. The walk is still worth showing, so
    the stage says where the call would have gone."""
    cfg = _pipeline_cfg(["dedup", {"type": "http", "name": "brain", "url": "https://brain.example/triage"}, "routes"])
    client = _StubClient(_StubResponse(200, {"action": "drop", "skip_code": "brain_said_no"}))

    result = await handle_hook(
        store, cfg, cfg.sources["ci"], {"job": "deploy", "status": "meh"}, now=1000.0, client=client, dry_run=True
    )

    assert client.requests == [], "a dry run must not reach the network"
    step = next(s for s in result["steps"] if s["gate"] == "brain")
    assert step["result"] == "would_post" and step["url"] == "https://brain.example/triage"
    # The verdict was never asked for, so the walk continues past the stage —
    # and says as much, rather than letting the steps below be read as the
    # brain's opinion.
    assert result["dry_run"] is True and result["outcome"] == "routed"
    assert "dry run" in step["note"]
    assert await store.recent_events(1) == [], "and it still leaves nothing behind"


def test_generic_signature_covers_the_exact_wire_bytes():
    """The regression that motivated bytes-exact builders: signature must be
    HMAC of the payload AS SENT, not of a private canonicalization."""
    channel = Channel(
        name="ww",
        type="generic",
        url="https://ww.example/v1/webhook",
        secret="wwsec",
        signature_header="X-Webhook-Signature",
    )
    message = {
        "event_id": 1,
        "source": "ci",
        "title": "t",
        "body": "b",
        "level": "info",
        "fields": {"z": "1", "a": "2"},
    }
    _url, payload, headers = build_request(channel, message, now=0.0)
    assert isinstance(payload, bytes), "signed payloads must be final bytes"
    assert headers["X-Webhook-Signature"] == hmac.new(b"wwsec", payload, hashlib.sha256).hexdigest()
    assert headers["content-type"] == "application/json"


def test_gate_matches_ci():
    """scripts/gate.sh must run what CI runs — a local list that is merely
    'close enough' is how a red CI arrives as a surprise. Adding a check to
    one requires adding it to the other in the same change.

    This test used to match by substring and skipped mypy entirely: both files
    could have lost the type check with nothing failing, and `pip_audit` was
    pinned without the flag that makes it usable on a runner. Commands are now
    pinned to the END of a line, because a substring is satisfied by
    `compileall -q hookrelay tests` whether or not another directory follows
    it — a contract test that passes while the contract has drifted is worse
    than no test.
    """
    from pathlib import Path

    here = Path(__file__).resolve().parent.parent
    gate = (here / "scripts" / "gate.sh").read_text()
    # hookrelay is one service in the hookstack repo, and GitHub only reads
    # workflows from the repo ROOT — so the file this gate is pinned to lives
    # one level up.
    ci = (here.parent / ".github" / "workflows" / "ci.yml").read_text()

    def runs(text: str, command: str) -> bool:
        """`$PY -m mypy hookrelay` and `- run: python -m mypy hookrelay` differ
        only in how the interpreter is spelled, so the tail is the contract."""
        return any(line.rstrip().endswith(command) for line in text.splitlines())

    # Every tool the gate runs, carrying the arguments that decide what it
    # covers.
    for command in (
        "compileall -q hookrelay tests",
        "ruff check hookrelay tests",
        "ruff format --check hookrelay tests",
        "mypy hookrelay",
        "bandit -q -r hookrelay",
        "pytest -q",
        "pip_audit --progress-spinner off",
    ):
        assert runs(gate, command), f"gate.sh does not run {command!r}"
        assert runs(ci, command), f"ci.yml does not run {command!r}"

    # Not commands: the files the inline steps read. Their names are the only
    # evidence in either file that those steps are still there.
    for marker in ("status.html", "examples/plugins", "config.example.yaml"):
        assert marker in gate, f"gate.sh is missing {marker!r}"
        assert marker in ci, f"ci.yml is missing {marker!r}"


def test_the_stack_gate_matches_its_workflow() -> None:
    """The root gate and ci-stack.yml drift too, and nothing was watching.

    Each service pins its own gate to its own workflow. The STACK gate — the
    cross-service checks, the ones no component can run because they compare
    services to each other — was pinned by nothing, and it had already drifted:
    `assert_weight.py` ran in scripts/gate.sh and in no workflow at all, so every
    ceiling it enforces was advisory on any machine but a maintainer's.

    Lives in hookrelay's suite for the dull reason that the stack has no suite of
    its own and the pipe is the first service. Nothing about it is hookrelay's.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent.parent
    gate = (root / "scripts" / "gate.sh").read_text()
    ci = (root / ".github" / "workflows" / "ci-stack.yml").read_text()

    for script in (
        "scripts/check-docs.py",
        "scripts/assert_design.py",
        "scripts/assert_agent_notes.py",
        "scripts/assert_locks.py",
        "scripts/assert_weight.py",
        "scripts/assert_copies.py",
        "scripts/assert_ordering.py",
    ):
        assert script in gate, f"scripts/gate.sh no longer runs {script}"
        assert script in ci, f"ci-stack.yml no longer runs {script}"

    # And every assert_*.py in the tree is run by SOMETHING. Two runners, because
    # two of these need a stack that is already up (they read a live ledger and a
    # config loaded the way the pipe loads it) and so belong to the smoke rather
    # than the gate. A check nobody invokes is the cheapest kind of green.
    smoke = (root / "scripts" / "stack-smoke.sh").read_text()
    for script in sorted(p.name for p in (root / "scripts").glob("assert_*.py")):
        assert script in gate or script in smoke, f"scripts/{script} is run by neither gate.sh nor stack-smoke.sh"
