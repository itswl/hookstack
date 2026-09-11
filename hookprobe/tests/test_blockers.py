"""A gap nobody can act on is not a finding, it is noise repeated.

Measured on the planning node before this existed: 11 `task` reports, 10 with
an `unknowns` section, 0 with anything an operator could act on, and the same
credential named in three consecutive ones.
"""

from __future__ import annotations

import asyncio
from typing import Any

from hookprobe import blockers

REPORT = """How it would be done: three steps, but I could not check the manifests.

```blocked
[{"kind": "credential", "name": "GITHUB_TOKEN", "answers": "which chart defines the app labels"},
 {"kind": "egress", "name": "api.github.com", "answers": "the same thing, if the token arrives"},
 {"kind": "question", "name": "how many days is long retention", "answers": "the policy value"},
 {"kind": "probe", "command": "GET _cat/indices/foo-*?v", "answers": "the daily volume per app"}]
```
"""


def test_a_gap_declares_its_kind_and_the_key_that_opens_it() -> None:
    gaps = blockers.extract(REPORT)
    assert [g["kind"] for g in gaps] == ["credential", "egress", "question", "probe"]
    assert gaps[0]["name"] == "GITHUB_TOKEN"
    # A probe carries the COMMAND, because the name of a thing you cannot
    # reach is not what the operator needs — the command is.
    assert gaps[3]["command"] == "GET _cat/indices/foo-*?v"


def test_a_gap_without_a_key_is_not_a_gap() -> None:
    """The whole point is actionability, so the shapes that carry no key are
    dropped rather than stored: an unknown kind, a missing `answers`, a probe
    with no command, a credential with no name."""
    for entry in (
        '{"kind": "vibes", "name": "x", "answers": "y"}',
        '{"kind": "credential", "name": "X"}',
        '{"kind": "probe", "answers": "y"}',
        '{"kind": "credential", "answers": "y"}',
    ):
        assert blockers.extract(f"```blocked\n[{entry}]\n```") == []
    assert blockers.extract("no block here") == []
    assert blockers.extract("```blocked\nnot json\n```") == []


def test_absent_and_empty_are_different_repairs(monkeypatch) -> None:
    """The half the agent cannot see, and the reason the same line came back
    three times. `os.environ.get` returns None for "nothing passes this to me"
    and "" for "something passes it, empty" — a compose line versus an .env
    line. From inside a container they look identical, so the report said the
    unactionable half."""
    gaps = blockers.extract(REPORT)
    absent = blockers.annotate(gaps, env={})
    assert absent[0]["reach"] == "absent" and "compose" in absent[0]["repair"]
    empty = blockers.annotate(gaps, env={"GITHUB_TOKEN": "  "})
    assert empty[0]["reach"] == "empty" and ".env" in empty[0]["repair"]
    ok = blockers.annotate(gaps, env={"GITHUB_TOKEN": "ghp_x"})
    assert ok[0]["reach"] == "set" and ok[0]["repair"] == ""
    # Presence only. A blocker that quoted the value would put a credential in
    # the case file, the audit and the chat.
    assert "ghp_x" not in str(ok)


def test_the_same_gap_across_investigations_is_a_number(tmp_path) -> None:
    """One report saying "no GitHub token" is a sentence somebody skims. Three
    saying it is a figure, and a figure is what gets the compose line written."""

    class FakeRun:
        def __init__(self, gaps: list[dict[str, Any]]) -> None:
            self.meta = {"blocked_on": gaps}

    token = {"kind": "credential", "name": "GITHUB_TOKEN", "answers": "the manifests"}
    once = {"kind": "egress", "name": "api.github.com", "answers": "the same"}
    tallied = blockers.tally([FakeRun([token, once]), FakeRun([token]), FakeRun([token]), FakeRun([])])
    assert tallied[0]["name"] == "GITHUB_TOKEN" and tallied[0]["runs"] == 3
    assert tallied[1]["name"] == "api.github.com" and tallied[1]["runs"] == 1
    assert blockers.tally([]) == []


def test_a_blocked_report_records_what_stopped_it(tmp_path):
    """End to end: the report names its gaps, the service lifts them onto the
    run, and the annotation says which repair each one needs."""
    from hookprobe.engine import EngineResult
    from hookprobe.runs import RunStore
    from hookprobe.service import RunService
    from tests.helpers import FakeEngine, make_settings

    async def scenario():
        settings = make_settings(tmp_path)
        engine = FakeEngine(result=EngineResult(text=REPORT, message_count=1))
        service = RunService(settings, engine, RunStore(tmp_path / "results"))
        service.start({"message": "Title: t\ngo", "sessionKey": "k1"})
        for _ in range(300):
            run = service.get("k1")
            if run and run.finished:
                return run
            await asyncio.sleep(0.01)
        raise AssertionError("never finished")

    run = asyncio.run(scenario())
    gaps = run.meta["blocked_on"]
    assert [g["kind"] for g in gaps] == ["credential", "egress", "question", "probe"]
    assert gaps[0]["reach"] in ("absent", "empty", "set")


def test_the_task_prompt_still_asks_for_all_four(tmp_path) -> None:
    """The parser is useless if the prompt stops asking, and a prompt erodes
    silently — nothing fails when a paragraph goes missing. So the contract is
    pinned: the four kinds, the try-before-declaring rule, and the instruction
    to put the ask in the opening sentence, which is the only part of a report
    the card quotes."""
    from hookprobe.events import _TASK_MESSAGE

    for kind in ("credential", "egress", "question", "probe"):
        assert f'"kind": "{kind}"' in _TASK_MESSAGE, kind
    assert "```blocked" in _TASK_MESSAGE
    assert "TRY BEFORE YOU DECLARE A GAP" in _TASK_MESSAGE
    assert "OPENING SENTENCE" in _TASK_MESSAGE
    # And the loop that makes a paste cheaper than a new investigation.
    assert "same investigation" in _TASK_MESSAGE
