"""The last hop of the remediation contract: the verdict on an approved
procedure is told where the report went.

Three verdicts, three tellings. A recovery holds it and says so at once; a
re-fire inside the window says it did not hold, failure-shaped; a window that
closes quietly is found by the sweep and told in the row's own thin words. Each
is told once, and the card names the verdict instead of calling itself an
investigation.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from hookprobe import actions, remediation
from hookprobe.engine import EngineResult
from hookprobe.notify import ReturnDelivery
from hookprobe.runs import COMPLETED, FAILED, Run, RunStore
from hookprobe.service import RunService
from tests.helpers import FakeEngine, approve_as_read, make_settings

REPORT = 'ok\n```remediation\n[{"action":"probe","command":"echo remediated","risk":"low"}]\n```\n'
KEY = "probe:alerts:99"


class _Recorder(ReturnDelivery):
    """A return delivery that keeps the body instead of posting it."""

    def __init__(self, settings: Any, store: Any) -> None:
        super().__init__(settings, store)
        self.bodies: list[dict[str, Any]] = []

    def _post_return(self, body: bytes) -> int:
        self.bodies.append(json.loads(body))
        return 200


def _executed(tmp_path: Path) -> tuple[RunService, Run, dict[str, Any]]:
    """A relay-born run whose one allowlisted step was approved and ran clean."""

    async def scenario() -> tuple[RunService, Run, dict[str, Any]]:
        tmp_path.mkdir(parents=True, exist_ok=True)
        allowlist = tmp_path / "allow.txt"
        allowlist.write_text("echo .*\n")
        settings = make_settings(tmp_path, remediation_allowlist=allowlist)
        engine = FakeEngine(result=EngineResult(text=REPORT, message_count=1))
        service = RunService(settings, engine, RunStore(tmp_path / "results"))
        service.start(
            {
                "message": "Title: t\ngo",
                "sessionKey": KEY,
                "_meta": {"source": "alerts", "title": "t", "event_id": 99, "correlation_id": "hr-99"},
            },
            origin="relay",
        )
        for _ in range(300):
            run = service.get(KEY)
            if run is not None and run.finished:
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("the investigation never finished")
        pid = run.meta["remediation_proposal"]
        approve_as_read(service, pid, actor="ou_operator")
        for _ in range(300):
            row = remediation.load(tmp_path, pid)
            if row is not None and row["status"] in (remediation.EXECUTED, remediation.FAILED):
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("the procedure never ran")
        assert row["status"] == remediation.EXECUTED, row
        return service, run, row

    return asyncio.run(scenario())


def _verdicts(service: RunService) -> list[Run]:
    return [r for r in service.list_runs(limit=50) if r.session_key.startswith("probe:outcome:")]


def test_a_recovery_tells_the_chat_the_fix_held_once(tmp_path: Path) -> None:
    service, run, row = _executed(tmp_path)
    assert service.record_recovery("alerts", "t", event_id=5) is not None

    notice = service.get(f"probe:outcome:{row['id']}")
    assert notice is not None, "the verdict reached no chat"
    assert notice.status == COMPLETED and notice.error is None, "good news is not failure-shaped"
    assert notice.meta["notice"] == "outcome" and notice.meta["outcome"] == remediation.HELD
    assert notice.meta["held_by"] == "recovery" and notice.meta["approved_by"] == "ou_operator"
    assert notice.meta["correlation_id"] == "hr-99" and notice.meta["event_id"] == 99, "cut against the alert's chain"
    assert notice.cost_usd == 0.0 and "HELD" in notice.text and "echo remediated" in notice.text
    assert actions.declare(notice, tmp_path) == [], "nothing to approve, nothing to rule on"

    # Told once: the recovery door is a redelivery path and the sweep runs every minute.
    assert service.record_recovery("alerts", "t", event_id=5) is not None
    assert service.sweep_outcomes() == 0
    assert len(_verdicts(service)) == 1


def test_a_refire_inside_the_window_tells_the_chat_it_did_not_hold(tmp_path: Path) -> None:
    service, run, row = _executed(tmp_path)
    assert service.record_refire(run, event_id=6), "the re-fire inside the window is the evidence"

    notice = service.get(f"probe:outcome:{row['id']}")
    assert notice is not None and notice.status == FAILED
    assert notice.error is not None and notice.error.startswith("did not hold")
    assert notice.meta["outcome"] == remediation.DID_NOT_HOLD and notice.meta["held_by"] == "refire"
    assert "fresh look" in notice.text
    assert service.sweep_outcomes() == 0 and len(_verdicts(service)) == 1


def test_the_quiet_window_is_found_by_the_sweep_and_told_in_thin_words(tmp_path: Path) -> None:
    service, run, row = _executed(tmp_path)
    until = float(row["verifying_until"])
    assert service.sweep_outcomes(now=until - 1) == 0, "the window is still open: nothing to tell yet"
    assert service.get(f"probe:outcome:{row['id']}") is None

    assert service.sweep_outcomes(now=until + 1) == 1
    notice = service.get(f"probe:outcome:{row['id']}")
    assert notice is not None and notice.status == COMPLETED and notice.meta["held_by"] == "window"
    assert "no re-fire within the window" in notice.text and "Thin evidence" in notice.text
    assert service.sweep_outcomes(now=until + 2) == 0, "told once"


def test_the_verdict_card_names_itself_and_takes_the_recoverys_colour(tmp_path: Path) -> None:
    service, run, row = _executed(tmp_path)
    service.record_recovery("alerts", "t", event_id=5)
    notice = service.get(f"probe:outcome:{row['id']}")
    assert notice is not None

    recorder = _Recorder(
        make_settings(tmp_path, return_url="http://pipe.invalid/probe-notify"), RunStore(tmp_path / "results")
    )
    asyncio.run(recorder.deliver(notice, (0.0,)))
    posted = recorder.bodies[-1]
    assert posted["meta"]["alert_name"] == "t · fix held"
    assert posted["meta"]["is_recovery"] is True, "the condition did end: the card takes the recovery's colour"
    assert posted["analysis"]["event_type"] == "remediation-outcome"
    assert posted["meta"]["session_key"] == f"probe:outcome:{row['id']}"
    assert posted["meta"]["correlation_id"] == "hr-99" and posted["meta"]["event_id"] == 99
    assert posted["meta"]["cost_usd"] == 0.0 and posted["actions"] == []
    assert "HELD" in posted["analysis"]["summary"]

    # The weak form is a report, not a recovery: same title, no recovery colour.
    service2, run2, row2 = _executed(tmp_path / "second")
    service2.sweep_outcomes(now=float(row2["verifying_until"]) + 1)
    quiet = service2.get(f"probe:outcome:{row2['id']}")
    recorder2 = _Recorder(
        make_settings(tmp_path / "second", return_url="http://pipe.invalid/probe-notify"),
        RunStore(tmp_path / "second" / "results"),
    )
    asyncio.run(recorder2.deliver(quiet, (0.0,)))
    weak = recorder2.bodies[-1]
    assert weak["meta"]["alert_name"] == "t · fix held" and "is_recovery" not in weak["meta"]
    assert "Thin evidence" in weak["analysis"]["summary"]


def test_a_procedure_that_did_not_run_clean_has_no_verdict_to_tell(tmp_path: Path) -> None:
    """A step that exited non-zero has nothing to hold; the failure was told
    as itself when it happened. Neither the sweep nor a direct call invents a
    verdict for it."""
    service, run, row = _executed(tmp_path)
    row["status"] = remediation.FAILED
    remediation.save(tmp_path, row)
    assert service.sweep_outcomes(now=float(row["verifying_until"]) + 1) == 0
    assert service.report_outcome(row, remediation.HELD, "whatever") is None
    assert _verdicts(service) == []
