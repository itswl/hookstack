"""The one human step in a chain that is otherwise automatic.

An operator reads a plan and decides that one is worth acting on. Today that
decision is expressed by copying the plan into an agent on their own laptop,
with every credential they own and no record of what ran. The decision is not
missing — it is expensive, and it buys the least accountable execution available.
This is the same decision, one click, into a node whose permissions are declared.
"""

from __future__ import annotations

import json

import pytest

from hookprobe import handoff


class _Run:
    def __init__(self, key: str = "probe:watch:7", text: str = "the plan", cost: float = 0.42) -> None:
        self.session_key, self.text, self.cost_usd = key, text, cost


def test_it_is_off_until_a_door_is_named() -> None:
    """The shipping default. A runner that hands off by accident is a runner
    that started something with credentials nobody clicked for."""
    with pytest.raises(handoff.NotConfigured):
        handoff.send("", "s", _Run(), "the plan")


def test_a_run_with_nothing_to_say_is_refused() -> None:
    """An empty handoff is a paid run started on nothing at all — and the node
    behind that door is the one with write credentials."""
    for empty in ("", "   ", "\n\n"):
        with pytest.raises(handoff.NotFinished):
            handoff.send("http://pipe/hook/x", "s", _Run(), empty)


def test_two_clicks_produce_one_fingerprint() -> None:
    """Dedup is the pipe's job and is left to it: the payload is deterministic
    per run, so a second click arrives as the same event and the door's dedup
    stage records it as a duplicate instead of buying a second run.

    A local set of already-handed-off keys would be a second copy of a decision
    the ledger already holds, and it would not survive a restart.
    """
    run = _Run()
    first, second = handoff.payload_for(run, "the plan"), handoff.payload_for(run, "the plan")
    assert first == second
    assert run.session_key in first["title"], "the key is what makes it deterministic AND readable"


def test_the_report_travels_whole_and_the_plans_key_with_it() -> None:
    """`session` is not decoration: it lets the receiving runner be pointed at
    the case file that produced this — the investigation, not just its
    conclusion."""
    payload = handoff.payload_for(_Run(key="probe:watch:9"), "step one\nstep two")
    assert payload["message"] == "step one\nstep two"
    assert payload["session"] == "probe:watch:9"


def test_it_signs_what_it_sends(monkeypatch) -> None:
    """The door on the other side is a pipe door like any other, so it is
    verified like any other. The signature covers the exact bytes posted."""
    seen: dict[str, object] = {}

    class _Response:
        status = 200

        def read(self) -> bytes:
            return b'{"outcome":"routed"}'

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    def fake_urlopen(request, timeout=0):
        seen["url"] = request.full_url
        seen["body"] = request.data
        seen["headers"] = {k.lower(): v for k, v in request.headers.items()}
        return _Response()

    monkeypatch.setattr(handoff.urllib.request, "urlopen", fake_urlopen)
    answer = handoff.send("http://pipe/hook/plan-approved", "shhh", _Run(), "the plan")

    assert seen["url"] == "http://pipe/hook/plan-approved"
    assert json.loads(seen["body"])["message"] == "the plan"  # type: ignore[arg-type]
    headers = seen["headers"]  # type: ignore[assignment]
    assert "x-hook-signature" in headers and "x-hook-timestamp" in headers  # type: ignore[operator]
    assert answer["pipe"] == {"outcome": "routed"}, "the pipe's own words, not a summary of them"


def _client(tmp_path, **overrides):
    from fastapi.testclient import TestClient

    from hookprobe.app import create_app
    from hookprobe.runs import Run, RunStore
    from hookprobe.service import RunService
    from tests.helpers import FakeEngine, make_settings

    settings = make_settings(tmp_path, **overrides)
    store = RunStore(tmp_path / "results")
    client = TestClient(create_app(settings, RunService(settings, FakeEngine(), store)))
    return client, store, {"Authorization": f"Bearer {settings.token}"}, Run


def _settled(store, Run, key: str, text: str):
    run = Run(session_key=key, run_id="r-" + key, status="succeeded", text=text)
    store.create(run)
    return run


def test_the_endpoint_separates_off_from_missing_from_unfinished(tmp_path) -> None:
    """Three different answers, because a button that failed is owed a reason:

    404 the session does not exist · 501 the feature exists and is switched off,
    which is not the same as a missing route · 409 the run has nothing to hand
    over yet, which is the one an impatient click produces.
    """
    client, store, headers, Run = _client(tmp_path)
    assert client.post("/v1/runs/nope/handoff", headers=headers).status_code == 404

    _settled(store, Run, "probe:watch:1", "the plan")
    assert client.post("/v1/runs/probe:watch:1/handoff", headers=headers).status_code == 501

    wired, store2, headers2, Run2 = _client(tmp_path / "b", handoff_url="http://pipe/hook/x", handoff_secret="s")
    _settled(store2, Run2, "probe:watch:2", "")
    assert wired.post("/v1/runs/probe:watch:2/handoff", headers=headers2).status_code == 409


def test_the_button_needs_the_operators_token(tmp_path) -> None:
    """It is the one route on this page that starts something with credentials."""
    client, _, _, _ = _client(tmp_path, handoff_url="http://pipe/hook/x")
    assert client.post("/v1/runs/anything/handoff").status_code == 401
