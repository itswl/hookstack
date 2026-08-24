"""The board says which model was ASKED FOR; it must also say where."""

from __future__ import annotations

import asyncio

from hookprobe.settings import _endpoint_host


def test_the_host_is_taken_and_nothing_else_is() -> None:
    """Host only. A base URL is not supposed to carry a credential, and "not
    supposed to" is not a reason to render one on a shared board."""
    assert _endpoint_host("https://open.bigmodel.cn/api/anthropic") == "open.bigmodel.cn"
    assert _endpoint_host("https://api.deepseek.com") == "api.deepseek.com"
    assert _endpoint_host("https://user:secret@gateway.example/v1?key=abc") == "gateway.example"


def test_an_unset_base_url_means_anthropic_itself() -> None:
    """Empty is not unknown: the SDK's default IS api.anthropic.com, and calling
    that "unknown" would make the honest case the noisy one."""
    assert _endpoint_host("") == "api.anthropic.com"
    assert _endpoint_host("   ") == "api.anthropic.com"


def test_the_endpoint_is_stamped_on_the_run_not_resolved_at_display_time(tmp_path) -> None:
    """Per run, because the destination MOVES.

    Every run on this deployment records `claude-opus-5`, and they were served by
    Anthropic, then DeepSeek, then BigModel — all within a month. A board that
    resolved the endpoint from current config would relabel history to whatever
    is configured today, which is the failure this field exists to prevent.
    """
    from hookprobe.runs import COMPLETED, Run, RunStore

    store = RunStore(tmp_path)
    old = Run(run_id="old", session_key="old", status=COMPLETED)
    old.model, old.model_endpoint = "claude-opus-5", "api.deepseek.com"
    store.checkpoint(old)
    new = Run(run_id="new", session_key="new", status=COMPLETED)
    new.model, new.model_endpoint = "claude-opus-5", "open.bigmodel.cn"
    store.checkpoint(new)

    fresh = RunStore(tmp_path)
    read_old, read_new = fresh.get("old"), fresh.get("new")

    assert read_old is not None and read_new is not None
    assert read_old.model == read_new.model, "the requested name is the same, which is the whole problem"
    assert (read_old.model_endpoint, read_new.model_endpoint) == ("api.deepseek.com", "open.bigmodel.cn")


def test_a_started_run_carries_the_endpoint_from_settings(tmp_path) -> None:
    """Through the real start path, not by setting the field by hand.

    The first version of this file tested persistence only — build a Run, assign
    the field, read it back — and passed while the wiring was broken. Production
    then wrote `model_endpoint: ""` on a run whose `model` was set by the line
    directly above the one I had added: the backfill I patched runs on RESUME,
    and a new run gets its model from the `Run(...)` constructor, which I had not
    touched. A test that constructs the object cannot see that.
    """
    from hookprobe.runs import RunStore
    from hookprobe.service import RunService
    from tests.helpers import FakeEngine, make_settings

    async def settle(service, key, deadline=3.0):
        """Local rather than imported: wait_finished lives in another test module,
        and a test importing a sibling test is a dependency nobody expects."""
        loop_end = asyncio.get_running_loop().time() + deadline
        while asyncio.get_running_loop().time() < loop_end:
            run = service.get(key)
            if run is not None and run.finished_at:
                return run
            await asyncio.sleep(0.01)
        raise AssertionError(f"run {key} never finished")

    async def scenario():
        settings = make_settings(tmp_path, model_endpoint="open.bigmodel.cn")
        service = RunService(settings, FakeEngine(), RunStore(tmp_path / "results"))
        service.start({"sessionKey": "s1", "message": "hello"})
        return await settle(service, "s1")

    run = asyncio.run(scenario())

    assert run.model == "claude-opus-5", "the name asked for"
    assert run.model_endpoint == "open.bigmodel.cn", "and where it was asked"
