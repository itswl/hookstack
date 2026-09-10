"""The node demonstrating its claims — and, mostly, refusing to claim more.

The point of this endpoint is that it EXECUTES the boundaries rather than
reporting the settings that were supposed to create them. So the tests that
matter are the ones proving it fails when the boundary is absent, and never
passes when it could not look.
"""

from __future__ import annotations

import asyncio
from typing import Any

from hookprobe import audit, selftest
from tests.helpers import make_settings


def _check_that_held() -> dict[str, Any]:
    return {"name": "a boundary that held", "held": True, "detail": "", "does_not_stop": ""}


def _run(settings) -> dict[str, Any]:
    return asyncio.run(selftest.run(settings))


def test_a_check_that_could_not_run_is_never_a_pass() -> None:
    """The whole hazard this file exists for. A green board assembled out of
    checks that quietly skipped is worse than no board — it is the same shape
    as a price knob no compose could pass, where every surface read fine.

    Against `_roll_up` with a made-up mix, because this is about the ARITHMETIC
    over the checks and not about performing them: a subprocess, two posture
    CLIs and a socket are the price of demonstrating a boundary, and no part of
    that price buys anything here.
    """
    skipped = {"name": "unprovable", "held": None, "detail": "", "does_not_stop": ""}
    report = selftest._roll_up([_check_that_held(), skipped])

    assert report["held"] is True, "an unprovable check must not drag a true report down"
    assert report["demonstrated"] == 1, "nor count toward what was demonstrated"
    assert report["unproven"] == ["unprovable"] and report["failed"] == []

    broke = {"name": "broken", "held": False, "detail": "", "does_not_stop": ""}
    worse = selftest._roll_up([_check_that_held(), skipped, broke])
    assert worse["held"] is False and worse["failed"] == ["broken"]

    # And nothing is silently dropped: every check is still on the page.
    assert len(worse["checks"]) == 3


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
    guard = selftest.guard_refuses(make_settings(tmp_path, workdir=tmp_path))
    assert guard["held"] is False
    assert "egress bypass" in guard["detail"]
    # And one absent boundary takes the whole report down — asserted on the
    # assembly rather than by paying for another full run.
    assert selftest._roll_up([guard, _check_that_held()])["held"] is False


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
    egress = selftest.egress_refuses()
    assert egress["held"] is None
    assert egress["name"] in selftest._roll_up([egress])["unproven"]


def test_the_audit_chain_is_walked_not_asserted(tmp_path) -> None:
    """This row reported `null` from the day the endpoint shipped, which is
    what made it worth building. A node with nothing chained yet is STILL
    `null`: having recorded no linked lines demonstrates nothing."""

    settings = make_settings(tmp_path, workdir=tmp_path)

    empty = selftest.audit_is_tamper_evident(make_settings(tmp_path / "fresh", workdir=tmp_path / "fresh"))
    assert empty["held"] is None and "no chained lines" in empty["detail"]

    for i in range(3):
        audit.append(tmp_path / "audit", {"ts": i, "tool": "Bash", "detail": f"cmd {i}"})
    row = selftest.audit_is_tamper_evident(settings)
    assert row["held"] is True and "3 linked line(s) verify" in row["detail"]

    assert audit.verify_chain(tmp_path / "audit")["intact"] is True


def test_the_probe_it_writes_is_attributed_to_the_selftest(tmp_path) -> None:
    """Its gate probe is a real refusal and is recorded as one — the honest
    thing for it to do. But under `probe:selftest:0`, not a real run's key, or
    it would inflate the per-session refusal count that exists to spot a run
    which kept asking for what it may not have."""
    settings = make_settings(tmp_path, workdir=tmp_path)
    assert selftest.gate_refuses(settings)["held"] is True

    written = "".join(p.read_text(encoding="utf-8") for p in (tmp_path / "audit").glob("*.jsonl"))
    assert "probe:selftest:0" in written
    assert audit.trips(tmp_path / "audit", "probe:selftest:0", since=0) >= 1
    assert audit.trips(tmp_path / "audit", "probe:a-real-run:1", since=0) == 0


def test_a_doctored_audit_takes_the_whole_report_down(tmp_path) -> None:
    """The endpoint has to NOTICE a tampered record, not merely be able to."""
    import json

    settings = make_settings(tmp_path, workdir=tmp_path)
    for i in range(3):
        audit.append(tmp_path / "audit", {"ts": i, "tool": "Bash", "detail": f"cmd {i}"})
    path = next((tmp_path / "audit").glob("*.jsonl"))
    lines = path.read_text(encoding="utf-8").splitlines()
    doctored = json.loads(lines[1])
    doctored["detail"] = "nothing to see"
    lines[1] = json.dumps(doctored, ensure_ascii=False)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    row = selftest.audit_is_tamper_evident(settings)
    assert row["held"] is False and "CHAIN BREAKS AT" in row["detail"]
    assert selftest._roll_up([row])["held"] is False


def test_every_check_says_what_it_does_not_cover(tmp_path) -> None:
    """The same discipline as containment.md's third column. A check that only
    reports a pass teaches its reader that the boundary is total.

    One run, three fields. It was parametrised over the field names, which paid
    for a full selftest — subprocess, posture CLIs, sockets — three times to
    assert that three keys exist.
    """
    report = _run(make_settings(tmp_path, workdir=tmp_path))
    for check in report["checks"]:
        assert {"name", "detail", "does_not_stop"} <= set(check)
        assert check["held"] in (True, False, None)
    # The one full run in this file, so it also carries the end-to-end shape:
    # the arithmetic above is tested against `_roll_up`, and this proves the
    # real report obeys it too.
    ran = [c for c in report["checks"] if c["held"] is not None]
    assert report["demonstrated"] == len(ran) and report["held"] == all(c["held"] for c in ran)


def test_could_not_ask_is_unproven_not_failed(tmp_path, monkeypatch) -> None:
    """Found by production on this endpoint's first run, against itself: a
    blocking loopback call inside the async handler held the event loop, the
    service could not answer its own request, and the report said the boundary
    FAILED. "I could not look" and "the boundary broke" are different answers,
    and merging them is the exact thing this file refuses everywhere else."""

    def refuses_to_connect(*_a: Any, **_k: Any) -> Any:
        raise OSError("connection refused")

    monkeypatch.setattr(selftest.urllib.request, "urlopen", refuses_to_connect)
    settings = make_settings(tmp_path, workdir=tmp_path, token="t", agent_token="a")
    token = asyncio.run(selftest.agent_token_cannot_write(settings))
    assert token["held"] is None
    rolled = selftest._roll_up([token])
    assert token["name"] in rolled["unproven"] and token["name"] not in rolled["failed"]


def test_the_loopback_check_does_not_block_the_loop(tmp_path) -> None:
    """The fix, pinned: it must be awaited off the event loop, or the service
    deadlocks on its own request exactly as production did."""
    import inspect

    assert inspect.iscoroutinefunction(selftest.agent_token_cannot_write)
    source = inspect.getsource(selftest.agent_token_cannot_write)
    assert "to_thread" in source, "a blocking urlopen here holds the loop that has to answer it"
