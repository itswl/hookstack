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


# ── the watch: a check nobody runs has never caught anything ──────────────────


def _watch(tmp_path, monkeypatch, verdict, *, alarm_url: str = "https://alarm.invalid/x", sent: bool = True):
    """A Watch whose selftest returns `verdict` and whose alarm is recorded."""
    told: list[str] = []

    async def fake_run(_settings):
        return dict(verdict)

    async def fake_alarm(text: str) -> bool:
        told.append(text)
        return sent

    monkeypatch.setattr(selftest, "run", fake_run)
    settings = make_settings(tmp_path, workdir=tmp_path, alarm_url=alarm_url, selftest_every_seconds=3600)
    return selftest.Watch(settings, fake_alarm), told


def _verdict(**kw):
    base = {
        "held": True,
        "checked_at": 1.0,
        "demonstrated": 7,
        "unproven": [],
        "failed": [],
        "checks": [{"name": "n", "held": True, "detail": "d"}],
    }
    return {**base, **kw}


def test_a_holding_watch_says_nothing_and_records_the_verdict(tmp_path, monkeypatch) -> None:
    """An alarm channel that fires when nothing is wrong gets muted by a human,
    which is how a boundary check becomes decorative a second way."""
    watch, told = _watch(tmp_path, monkeypatch, _verdict())
    result = asyncio.run(watch.once())
    assert result["held"] is True and told == []
    assert watch.last is not None and watch.last["demonstrated"] == 7


def test_a_broken_boundary_reaches_the_alarm_and_says_so(tmp_path, monkeypatch) -> None:
    broken = _verdict(
        held=False,
        failed=["the shell guard refuses a mutation"],
        checks=[{"name": "the shell guard refuses a mutation", "held": False, "detail": "kubectl delete ran"}],
    )
    watch, told = _watch(tmp_path, monkeypatch, broken)
    result = asyncio.run(watch.once())
    assert result["alarm"] == "sent"
    assert told and "the shell guard refuses a mutation" in told[0] and "kubectl delete ran" in told[0]


def test_a_boundary_that_broke_where_nobody_could_be_told_says_which(tmp_path, monkeypatch) -> None:
    """The state that must be visible rather than inferred. A failing check on a
    node with no alarm channel, and one whose quiet window swallowed the news,
    are different situations with the same silence — and an operator reading the
    ops page has to be able to tell them apart. A boolean cannot."""
    broken = _verdict(held=False, failed=["egress refuses an unlisted destination"])
    quiet, _ = _watch(tmp_path, monkeypatch, broken, sent=False)
    assert asyncio.run(quiet.once())["alarm"] == "suppressed"

    deaf, told = _watch(tmp_path, monkeypatch, broken, alarm_url="")
    assert asyncio.run(deaf.once())["alarm"] == "no channel"
    assert told == [], "no channel means the alarm was never even attempted"


def test_unproven_is_neither_alarmed_nor_hidden(tmp_path, monkeypatch, caplog) -> None:
    """`held: true` with a check that could not run is not seven boundaries
    holding, and it is not a failure either. It warns."""
    watch, told = _watch(tmp_path, monkeypatch, _verdict(demonstrated=6, unproven=["the agent's bearer cannot write"]))
    with caplog.at_level("WARNING"):
        assert asyncio.run(watch.once())["held"] is True
    assert told == []
    assert "could not run" in caplog.text


def test_the_watch_survives_a_pass_that_raises(tmp_path, monkeypatch) -> None:
    """A watch that dies on one bad pass is worse than no watch: the ops page
    freezes on its last good verdict and nothing says the watching stopped."""
    calls = {"n": 0}

    async def sometimes_explodes(_settings):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("a subprocess went missing")
        return _verdict()

    async def alarm(_text: str) -> bool:
        return True

    monkeypatch.setattr(selftest, "run", sometimes_explodes)
    settings = make_settings(tmp_path, workdir=tmp_path, selftest_every_seconds=3600)
    watch = selftest.Watch(settings, alarm)

    async def two_passes():
        slept: list[float] = []

        async def no_sleep(seconds):
            slept.append(seconds)
            if len(slept) >= 3:
                raise asyncio.CancelledError

        monkeypatch.setattr(selftest.asyncio, "sleep", no_sleep)
        with pytest.raises(asyncio.CancelledError):
            await watch.loop(3600)
        return slept

    slept = asyncio.run(two_passes())
    assert calls["n"] == 2, "the raising pass did not stop the loop"
    assert slept[0] == 60, "the first pass waits: a process still starting is not one under test"


def test_the_engine_endpoint_check_answers_from_a_real_call(monkeypatch) -> None:
    """The outage of 2026-09-21/22 — gateway 404 at the root for ~33h on prod —
    sailed past all seven boundaries, because none asked whether the engine can
    run. This check asks with a real one-token completion, not a liveness GET:
    the dead-gateway symptom WAS an HTTP response (404), which "server
    answered" would have read as healthy.

    The env vars are SET, not ambient: on the developer's machine the shell
    carries a real ANTHROPIC_BASE_URL/TOKEN (it must, to run anything), and a
    test that read them passed there and failed on CI with held=None — three
    red commits before anyone read CI back. A test of "the check answers" owns
    the whole question, including the env it reads."""
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://gateway.invalid")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "sk-test-not-a-real-key")
    settings = make_settings(None, workdir=None, model="gpt-5.6-luna")

    def ok():
        return 0, "200"

    def dead_gateway():
        return 0, "404"

    def refused():
        return 7, ""

    import asyncio

    got = asyncio.run(selftest.engine_endpoint_answers(settings, ask=ok))
    assert got["held"] is True and "HTTP 200" in got["detail"]
    got = asyncio.run(selftest.engine_endpoint_answers(settings, ask=dead_gateway))
    assert got["held"] is False and "404" in got["detail"], "an answered 404 is the outage, not health"
    got = asyncio.run(selftest.engine_endpoint_answers(settings, ask=refused))
    assert got["held"] is False


def test_the_engine_endpoint_check_is_unproven_without_engine_env(monkeypatch) -> None:
    """A node with no engine env in this process is not wrong — it is a
    question this check cannot ask. `held: null`, never a pass."""
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    settings = make_settings(None, workdir=None, model="m")
    got = asyncio.run(selftest.engine_endpoint_answers(settings, ask=lambda: (0, "200")))
    assert got["held"] is None


def test_a_broken_chain_is_never_reported_as_unproven(monkeypatch, tmp_path) -> None:
    """2026-09-17..22 on production: a window-seeding bug in verify_chain made
    it return checked=0 AND intact=False, and this check's early return tested
    `checked == 0` first — so six days of "the chain breaks at line 1" were
    reported as `unproven: no chained lines yet`. An unproven that is really a
    break is the worst of both: no alarm, and a log line nobody reads. The
    order is the semantics: intact is tested first, always."""
    settings = make_settings(tmp_path, workdir=tmp_path)

    def broken_window(_dir):
        return {"intact": False, "checked": 0, "unchained": 0, "broken_at": "2026-09-17.jsonl:1"}

    monkeypatch.setattr(selftest.audit, "verify_chain", broken_window)
    got = selftest.audit_is_tamper_evident(settings)
    assert got["held"] is False, "a break with zero checked lines is a BREAK"
    assert "2026-09-17.jsonl:1" in got["detail"]
