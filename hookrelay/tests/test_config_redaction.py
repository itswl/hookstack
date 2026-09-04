"""GET /config promised redaction it did not do.

The docstring read "the running config with secrets redacted" while the handler
returned the file's bytes unchanged. It was true only by convention — every
config in this family writes `secret: ${NAME}` — and the failure mode is quiet:
somebody reads the promise, inlines a credential, and an endpoint hands it to
anyone holding the admin token.

What must NOT be masked is the other half of this, and the reason the pattern
excludes those cases itself rather than filtering afterwards.
"""

from __future__ import annotations

from hookrelay.app import _redact_secrets

CONFIG = """sources:
  - name: ww
    secret: ${WW_RELAY_SECRET}
    title: "{meta.alert_name}"
  - name: judge-notify
    # A loopback hop inside one deployment.
    secret: ""
    title: "{meta.alert_name}"
  - name: legacy
    secret: hunter2-the-real-one
channels:
  - name: to-lark
    type: feishu
    url: https://open.example/hook/URL-CARRIED-TOKEN
    secret: 'quoted-literal'
"""


def test_an_inline_secret_is_masked() -> None:
    out = _redact_secrets(CONFIG)
    assert "hunter2-the-real-one" not in out
    assert "quoted-literal" not in out
    assert out.count("secret: <redacted>") == 2


def test_an_unsigned_door_stays_visible() -> None:
    """The one thing this must never do. `secret: ""` says the door is unsigned,
    which is a fact an admin reading this needs — masking it would make an open
    door and a closed one look identical."""
    assert 'secret: ""' in _redact_secrets(CONFIG)


def test_a_reference_stays_visible() -> None:
    """The name is the useful half and the value is not in this file anyway."""
    assert "secret: ${WW_RELAY_SECRET}" in _redact_secrets(CONFIG)


def test_everything_else_is_byte_identical() -> None:
    """A YAML round-trip would have dropped the comments, and in these files the
    comments carry the reasoning. Only the two secret lines may differ."""
    before = CONFIG.splitlines()
    after = _redact_secrets(CONFIG).splitlines()
    assert len(before) == len(after)
    changed = [i for i, (a, b) in enumerate(zip(before, after, strict=True)) if a != b]
    assert [before[i].strip() for i in changed] == ["secret: hunter2-the-real-one", "secret: 'quoted-literal'"]


def test_a_credential_bearing_url_is_NOT_masked() -> None:
    """Pinned as a KNOWN limit, not as acceptable-in-general: this cannot tell a
    Lark bot URL (whose token is in the path) from an internal service address,
    so it masks neither. The docstring says so and points at ${REF}; /topology is
    the view that prints host only."""
    assert "URL-CARRIED-TOKEN" in _redact_secrets(CONFIG)
