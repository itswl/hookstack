"""The node demonstrating its claims — and, mostly, refusing to claim more.

The point of this endpoint is that it EXECUTES the boundaries rather than
reporting the settings that were supposed to create them. So the tests that
matter are the ones proving it fails when the boundary is absent, and never
passes when it could not look.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from hookprobe import selftest
from tests.helpers import make_settings


def _run(settings) -> dict[str, Any]:
    return asyncio.run(selftest.run(settings))


def test_a_check_that_could_not_run_is_never_a_pass(tmp_path) -> None:
    """The whole hazard this file exists for. A green board assembled out of
    checks that quietly skipped is worse than no board — it is the same shape
    as a price knob no compose could pass, where every surface read fine."""
    report = _run(make_settings(tmp_path, workdir=tmp_path))
    for check in report["checks"]:
        assert check["held"] in (True, False, None)
        if check["held"] is None:
            assert check["name"] in report["unproven"]
            assert check["name"] not in report["failed"]
    # `held` is computed only over what actually ran.
    ran = [c for c in report["checks"] if c["held"] is not None]
    assert report["demonstrated"] == len(ran)
    assert report["held"] == all(c["held"] for c in ran)


def test_the_gate_check_actually_asks_the_gate(tmp_path) -> None:
    """In-process and through the subprocess path. The second is the one that
    was once simply absent — the hook command could not import hookprobe, the
    runtime logged it and carried on."""
    report = _run(make_settings(tmp_path, workdir=tmp_path))
    by_name = {c["name"]: c for c in report["checks"]}
    assert by_name["gate refuses a tool no posture permits"]["held"] is True
    assert by_name["the spawned gate answers and refuses"]["held"] is True


def test_it_fails_when_the_guard_would_let_the_bypass_through(tmp_path, monkeypatch) -> None:
    """Not a mock of the report — the guard itself is replaced with one that
    allows, and the endpoint has to notice."""
    monkeypatch.setattr(selftest, "bash_deny_reason", lambda command, mode: None)
    report = _run(make_settings(tmp_path, workdir=tmp_path))
    guard = next(c for c in report["checks"] if c["name"].startswith("the shell guard"))
    assert guard["held"] is False
    assert "egress bypass" in guard["detail"]
    assert report["held"] is False, "one absent boundary must take the whole report down"


def test_the_egress_check_never_connects_where_it_is_told(tmp_path) -> None:
    """An endpoint that opened a connection to a caller-supplied host would be
    the request-forgery hole this file argues against. The destination is a
    constant, and one that cannot resolve."""
    import inspect

    # The destination is a constant, and the function takes nothing that could
    # become one. `.invalid` is reserved by RFC 2606 and can never resolve, so
    # the refusal is the allowlist's and not a DNS accident.
    assert selftest.UNROUTABLE.endswith(".invalid")
    assert list(inspect.signature(selftest.egress_refuses).parameters) == []


def test_no_proxy_configured_is_unproven_not_broken(tmp_path, monkeypatch) -> None:
    """A laptop with no egress proxy has not failed a boundary; it has one this
    node cannot demonstrate. Those are different answers."""
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    report = _run(make_settings(tmp_path, workdir=tmp_path))
    egress = next(c for c in report["checks"] if c["name"].startswith("egress"))
    assert egress["held"] is None
    assert egress["name"] in report["unproven"]


def test_the_gap_it_cannot_close_is_on_the_same_page(tmp_path) -> None:
    """The audit is append-only JSONL with no chaining, so the claim a
    compliance reader most wants is the one this cannot make. Reported as
    `null` rather than omitted, because a boundary list that silently drops
    what it cannot prove is a boundary list nobody should trust."""
    report = _run(make_settings(tmp_path, workdir=tmp_path))
    audit = next(c for c in report["checks"] if "tamper-evident" in c["name"])
    assert audit["held"] is None
    assert "not built" in audit["detail"]


@pytest.mark.parametrize("field", ["does_not_stop", "detail", "name"])
def test_every_check_says_what_it_does_not_cover(tmp_path, field: str) -> None:
    """The same discipline as containment.md's third column. A check that only
    reports a pass teaches its reader that the boundary is total."""
    report = _run(make_settings(tmp_path, workdir=tmp_path))
    for check in report["checks"]:
        assert field in check
