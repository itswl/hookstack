"""The Jira scanner reports a change once.

Every change was reported in two adjacent rounds on a deployment running a
copy of this script (2026-10-09): the last change reported has exactly the
timestamp the state remembers, the next round's lookback still covers it, and
"not earlier than" let it through again. The rounds here run against a fake
Jira with a fixed set of changes; the second round must print nothing.

The Jira answers are fakes: no site, no credential, `req` replaced wholesale.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import jira_watch  # noqa: E402


def stamp(epoch: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S.000%z", time.localtime(epoch))


COLLEAGUE = {"accountId": "them", "displayName": "A Colleague"}
COMMENT = {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "please look"}]}]}


class FakeJira:
    """Two issues: one with a reassignment and a comment ten minutes ago, one
    merely created ten minutes ago — the two paths that each re-reported."""

    def __init__(self, now: float) -> None:
        self.now = now
        self.calls: list[tuple[str, dict | None]] = []

    def __call__(self, path: str, params: dict | None = None) -> dict:
        self.calls.append((path, params))
        if path.endswith("/myself"):
            return {"accountId": "me", "emailAddress": "Watched.Person@example.com", "timeZone": "Asia/Singapore"}
        if path.endswith("/search/jql"):
            return {"issues": [{"key": "PROJ-1"}, {"key": "PROJ-2"}]}
        ten_minutes_ago = stamp(self.now - 600)
        if path.endswith("/issue/PROJ-1"):
            return {
                "fields": {
                    "summary": "a ticket with activity",
                    "status": {"name": "In Progress"},
                    "assignee": {"displayName": "Watched Person"},
                    "reporter": COLLEAGUE,
                    "created": stamp(self.now - 7200),
                    "comment": {"comments": [{"created": ten_minutes_ago, "author": COLLEAGUE, "body": COMMENT}]},
                },
                "changelog": {
                    "histories": [
                        {
                            "created": ten_minutes_ago,
                            "author": COLLEAGUE,
                            "items": [{"field": "assignee", "fromString": None, "toString": "Watched Person"}],
                        }
                    ]
                },
            }
        if path.endswith("/issue/PROJ-2"):
            return {
                "fields": {
                    "summary": "a ticket just created",
                    "status": {"name": "To Do"},
                    "assignee": None,
                    "reporter": COLLEAGUE,
                    "created": ten_minutes_ago,
                    "comment": {"comments": []},
                },
                "changelog": {"histories": []},
            }
        raise AssertionError(f"unexpected request {path}")


@pytest.fixture
def jira(tmp_path, monkeypatch):
    monkeypatch.setattr(jira_watch, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setenv("ATL_ENV_FILE", str(tmp_path / "no-such.env"))
    monkeypatch.delenv("ATL_MENTION", raising=False)
    fake = FakeJira(time.time())
    monkeypatch.setattr(jira_watch, "req", fake)
    return fake


def test_a_change_is_reported_once_across_adjacent_rounds(jira, capsys):
    jira_watch.main()
    first = capsys.readouterr().out
    assert "PROJ-1" in first and "🔀" in first and "💬" in first
    assert "PROJ-2" in first and "🆕" in first
    jira_watch.main()
    assert capsys.readouterr().out == ""


def test_the_state_survives_the_round_and_is_written_whole(jira):
    jira_watch.main()
    state = json.loads(jira_watch.STATE_FILE.read_text(encoding="utf-8"))
    assert set(state["reported"]) == {"PROJ-1", "PROJ-2"}
    assert not list(jira_watch.STATE_FILE.parent.glob("*.tmp"))


def test_the_handle_comes_from_the_account_and_is_asked_once(jira):
    jira_watch.main()
    jira_watch.main()
    state = json.loads(jira_watch.STATE_FILE.read_text(encoding="utf-8"))
    assert state["mention"] == "watched.person"
    assert state["time_zone"] == "Asia/Singapore"
    assert sum(path.endswith("/myself") for path, _ in jira.calls) == 1
    searches = [params["jql"] for path, params in jira.calls if path.endswith("/search/jql")]
    assert searches and all('comment ~ "watched.person"' in jql for jql in searches)


def test_a_configured_handle_wins_over_the_derived_one(jira, monkeypatch):
    monkeypatch.setenv("ATL_MENTION", "someone-else")
    jira_watch.main()
    jql = next(params["jql"] for path, params in jira.calls if path.endswith("/search/jql"))
    assert 'comment ~ "someone-else"' in jql


def test_the_boundary_is_later_than_not_at_least():
    assert not jira_watch.fresh(100.0, last=100.0, cutoff=50.0)
    assert jira_watch.fresh(100.5, last=100.0, cutoff=50.0)
    assert jira_watch.fresh(60.0, last=None, cutoff=50.0)
    assert not jira_watch.fresh(40.0, last=None, cutoff=50.0)


def test_the_handle_is_the_email_local_part_or_nothing():
    assert jira_watch.mention_from_email("Watched.Person@example.com") == "watched.person"
    assert jira_watch.mention_from_email("") == ""
    assert jira_watch.mention_from_email(None) == ""
    assert jira_watch.mention_from_email("hidden") == ""


def test_an_unknown_zone_name_does_not_stop_the_round():
    assert jira_watch.account_zone("Asia/Singapore").key == "Asia/Singapore"
    assert jira_watch.account_zone("") is jira_watch.UTC8
    assert jira_watch.account_zone("No/Such_Zone") is jira_watch.UTC8
