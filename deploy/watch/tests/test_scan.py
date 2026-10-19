"""What the scanner does to the shape of a message before the model sees it."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import watch_scan  # noqa: E402


def test_fences_and_invisible_characters_do_not_reach_the_body():
    assert watch_scan.clean("a\u200bb\u202ec\ufeff") == "abc"
    assert watch_scan.clean("see ```sh\nrm -rf /\n``` now") == "see '''sh\nrm -rf /\n''' now"


def test_ordinary_text_is_untouched():
    line = "- 10-12 09:00 someone：请看一下 `foo` 的状态，`` 也算"
    assert watch_scan.clean(line) == line


# ── the cursor moves when the round is known to have landed ──────────────────
#
# The scanner used to write the new cursors before the model ran; a round whose
# run failed was never looked at again. Now the cursors are staged and settled
# next tick against the node's run record for the event the pipe numbered.


@pytest.fixture
def state(tmp_path, monkeypatch):
    results = tmp_path / "results"
    results.mkdir()
    monkeypatch.setattr(watch_scan, "SCAN", tmp_path / "scan.json")
    monkeypatch.setattr(watch_scan, "ROUND", tmp_path / "round.json")
    monkeypatch.setattr(watch_scan, "NODE_RESULTS", results)
    monkeypatch.setattr(watch_scan, "JIRA_STATE", tmp_path / "jira.json")
    monkeypatch.setattr(watch_scan, "JIRA_STAGED", tmp_path / "jira.staged.json")
    return tmp_path


def _staged(feeds, staged_at=1000.0, replays=0, first_at=None, first_feeds=None):
    return {
        "round_at": staged_at,
        "feeds": {"ops": 10.0},
        "staged": {
            "feeds": feeds,
            "staged_at": staged_at,
            "replays": replays,
            "first_at": first_at or staged_at,
            "first_feeds": first_feeds or feeds,
        },
    }


def _posted(state, event_id=77, fired_at=1001.0, posted=True, outcome="routed"):
    (state / "round.json").write_text(
        json.dumps({"posted": posted, "event_id": event_id, "outcome": outcome, "fired_at": fired_at})
    )


def _ran(state, event_id, status):
    (state / "results" / f"probe:watch-due:{event_id}.json").write_text(
        json.dumps({"session_key": f"probe:watch-due:{event_id}", "status": status})
    )


def test_a_finished_run_commits_the_staged_cursors_and_the_jira_copy(state):
    _posted(state)
    _ran(state, 77, "completed")
    (state / "jira.staged.json").write_text('{"jira_last_check": 2}')
    scan, verdict = watch_scan.reconcile(_staged({"ops": 20.0}), now=1100.0)
    assert verdict == "done" and scan["feeds"] == {"ops": 20.0} and "staged" not in scan
    assert json.loads((state / "jira.json").read_text()) == {"jira_last_check": 2}
    assert not (state / "jira.staged.json").exists()


def test_a_failed_run_puts_the_cursors_back_and_asks_for_a_replay(state):
    _posted(state)
    _ran(state, 77, "failed")
    (state / "jira.staged.json").write_text("{}")
    scan, verdict = watch_scan.reconcile(_staged({"ops": 20.0}), now=1100.0)
    assert verdict == "replay" and scan["feeds"] == {"ops": 10.0}
    assert scan["replay"]["replays"] == 1 and scan["replay"]["first_feeds"] == {"ops": 20.0}
    assert not (state / "jira.staged.json").exists(), "the Jira copy of a lost round is not kept"


@pytest.mark.parametrize(
    "why",
    ["not posted", "posted before the scan", "not routed", "no event id"],
)
def test_a_round_that_never_reached_a_run_is_lost(state, why):
    if why == "not posted":
        _posted(state, posted=False)
    elif why == "posted before the scan":
        _posted(state, fired_at=999.0)  # the timer died between the scan and this round's POST
    elif why == "not routed":
        _posted(state, outcome="skipped")
    else:
        _posted(state, event_id=None)
    _, verdict = watch_scan.reconcile(_staged({"ops": 20.0}), now=1100.0)
    assert verdict == "replay", why


def test_a_run_still_going_holds_the_tick_and_a_run_that_vanished_does_not(state):
    _posted(state, fired_at=1001.0)
    _, young = watch_scan.reconcile(_staged({"ops": 20.0}), now=1001.0 + 60)
    assert young == "pending", "no record yet and a minute old: the run may still be starting"
    _ran(state, 77, "running")
    _, running = watch_scan.reconcile(_staged({"ops": 20.0}), now=1001.0 + 600)
    assert running == "pending"
    _, stale = watch_scan.reconcile(_staged({"ops": 20.0}), now=1001.0 + watch_scan.RUN_TIMEOUT + 1)
    assert stale == "replay", "past the run timeout, a run with no end is a lost round"


def test_after_two_replays_and_an_hour_only_the_first_lost_round_is_skipped(state):
    _posted(state)
    _ran(state, 77, "failed")
    staged = _staged({"ops": 30.0}, staged_at=5000.0, replays=2, first_at=1000.0, first_feeds={"ops": 20.0})
    _posted(state, fired_at=5001.0)
    scan, verdict = watch_scan.reconcile(staged, now=5100.0)
    assert verdict == "gave-up"
    assert scan["feeds"] == {"ops": 20.0}, "the first failed round's cursors: its content is what is skipped"
    assert scan["skipped"]["to"] == 1000.0 and scan["skipped"]["attempts"] == 3
    assert "replay" not in scan


def test_two_replays_inside_the_hour_keep_trying(state):
    _posted(state, fired_at=1801.0)
    _ran(state, 77, "failed")
    staged = _staged({"ops": 30.0}, staged_at=1800.0, replays=2, first_at=1000.0, first_feeds={"ops": 20.0})
    _, verdict = watch_scan.reconcile(staged, now=1900.0)
    assert verdict == "replay", "three failures in fifteen minutes is an outage, not a poison round"


def test_nothing_staged_settles_nothing_and_drops_a_stray_jira_copy(state):
    (state / "jira.staged.json").write_text("{}")
    scan, verdict = watch_scan.reconcile({"round_at": 1.0, "feeds": {"ops": 10.0}}, now=2.0)
    assert verdict == "none" and scan["feeds"] == {"ops": 10.0}
    assert not (state / "jira.staged.json").exists()


def test_a_delivered_round_clears_the_skipped_note(state):
    _posted(state)
    _ran(state, 77, "completed")
    scan = _staged({"ops": 20.0})
    scan["skipped"] = {"from": 1.0, "to": 2.0, "attempts": 3}
    settled, verdict = watch_scan.reconcile(scan, now=1100.0)
    assert verdict == "done" and "skipped" not in settled


def test_the_run_record_is_found_by_its_key_even_under_another_file_name(state):
    (state / "results" / "something-else.json").write_text(
        json.dumps({"session_key": "probe:watch-due:5", "status": "completed"})
    )
    (state / "results" / "probe:watch-due:6.json").write_text(
        json.dumps({"session_key": "probe:watch-due:6", "status": "failed"})
    )
    assert watch_scan.run_record(6)["status"] == "failed"
    assert watch_scan.run_record(5) is None, "a record is found by its name, not by a scan of everything"
    assert watch_scan.run_record(9) is None


def test_jira_runs_against_a_staged_copy_of_its_state(state, monkeypatch):
    script = state / "fake_jira.py"
    script.write_text(
        "import json, os, pathlib\n"
        "p = pathlib.Path(os.environ['ATL_STATE_FILE'])\n"
        "before = json.loads(p.read_text()).get('jira_last_check') if p.exists() else None\n"
        "p.write_text(json.dumps({'from': before, 'jira_last_check': 9}))\n"
        "print('PROJ-1 [Open] a ticket')\n"
    )
    monkeypatch.setattr(watch_scan, "JIRA", str(script))
    (state / "jira.json").write_text(json.dumps({"jira_last_check": 4}))
    notes: list[str] = []
    out = watch_scan.run_jira(notes)
    assert out.startswith("PROJ-1") and notes == []
    committed = json.loads((state / "jira.json").read_text())
    assert committed == {"jira_last_check": 4}, "the committed state is untouched"
    staged = json.loads((state / "jira.staged.json").read_text())
    assert staged["from"] == 4, "the copy started from the committed state"
