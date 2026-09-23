"""A door that was expected to speak and did not.

The one failure nothing else in this pipe can see. Every other guard watches
something that HAPPENED — a delivery that failed, a route that matched nothing,
a verdict that came back wrong. An absence produces no delivery to retry and no
dead letter to raise, because the event that would have carried the failure is
the event that never arrived.

Measured rather than theorised. On 2026-09-04 a container timer could not read
its own brief; two rounds were lost, and the pipe, the investigator and the chat
all looked healthy the entire time. It was found by a person asking.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from hookrelay.config import Config
from hookrelay.pipeline import handle_hook

CFG = {
    "sources": [
        # A timer: silence is a defect.
        {
            "name": "watch-due",
            "secret": "",
            "title": "{t}",
            "body": "{b}",
            "level": "{l}",
            "expect_every_seconds": 1200,
        },
        # An alert door: silence is the GOOD outcome and must never alarm.
        {"name": "alerts", "secret": "", "title": "{t}", "body": "{b}", "level": "{l}"},
    ],
    "channels": [{"name": "to-me", "type": "generic", "url": "https://me.example/in"}],
    "routes": [{"name": "everything", "source": "*", "send_to": ["to-me"]}],
}


@pytest.fixture
def cfg() -> Config:
    return Config.from_dict(CFG)


def test_a_cadence_is_opt_in(cfg) -> None:
    """Most doors have no cadence: an alert source is quiet when nothing is
    wrong, and alarming on that would make the feature the first thing anybody
    switches off."""
    assert cfg.sources["watch-due"].expect_every_seconds == 1200
    assert cfg.sources["alerts"].expect_every_seconds == 0


async def test_a_door_that_never_spoke_does_not_alarm(store, cfg) -> None:
    """A deployment that just came up must not alarm about every timer whose
    first tick has not landed yet. `never` and `stopped` are different facts and
    only one of them is a defect."""
    assert await store.last_event_at("watch-due") is None


async def test_the_first_tick_to_notice_claims_it_and_the_rest_stay_quiet(store) -> None:
    """Claimed before the alarm is raised, like mark_escalated: a crash between
    the two costs one missed alarm rather than one per tick forever — and at a
    one-second worker interval, 'per tick forever' is a page every second."""
    assert await store.claim_absence("watch-due", 1000.0, 100.0) is True
    assert await store.claim_absence("watch-due", 1001.0, 100.0) is False
    assert await store.claim_absence("watch-due", 1002.0, 100.0) is False


async def test_speaking_again_rearms_it(store) -> None:
    """The claim is DELETED on recovery rather than stamped, so the NEXT silence
    alarms too. A stamp would mean a door that broke, recovered and broke again
    is reported once — and the second break is the one somebody has stopped
    expecting."""
    await store.claim_absence("watch-due", 1000.0, 100.0)
    await store.clear_absence("watch-due")
    assert await store.claim_absence("watch-due", 2000.0, 1900.0) is True


async def test_last_event_at_reads_the_newest(store, cfg) -> None:
    source = cfg.sources["watch-due"]
    for at in (100.0, 300.0, 200.0):
        await store.insert_event(
            "watch-due",
            f"fp-{at}",
            {"title": "tick", "body": "", "level": "info", "fields": {}},
            "{}",
            at,
        )
    assert await store.last_event_at("watch-due") == 300.0
    assert source.expect_every_seconds == 1200


def _at(weekday: int, hour: int, minute: int = 0) -> float:
    """A local timestamp on a fixed week. weekday 1 = Monday, matching the config."""
    import calendar
    import time

    # 2026-09-07 is a Monday. Build local time, not UTC, because the schedule is
    # read in the process's local zone — the whole point of the TZ line in compose.
    base = time.mktime((2026, 9, 7 + (weekday - 1), hour, minute, 0, 0, 0, -1))
    assert calendar.weekday(2026, 9, 7) == 0
    return base


def test_a_scheduled_door_is_not_expected_outside_its_hours() -> None:
    """Measured on the first evening after absence alarming shipped: the timer
    stopped at 19:40 as configured, and at 20:05 the pipe said it had 'said
    nothing for 25 minutes'. It would have said so every weekday evening."""
    src = Config.from_dict(
        {**CFG, "sources": [{**CFG["sources"][0], "expect_hours": "9-19", "expect_days": "1-5"}]}
    ).sources["watch-due"]
    assert src.expected_now(_at(1, 20, 5)) is False, "Monday 20:05 — the clock is off duty"
    assert src.expected_now(_at(6, 12, 0)) is False, "Saturday noon — the clock is off duty all day"
    assert src.expected_now(_at(1, 14, 0)) is True, "Monday 14:00 — it had better be ticking"


def test_the_window_opening_is_a_grace_not_an_alarm() -> None:
    """09:00 Monday: silent since Friday, inside the window, and the first tick
    lands one second from now. Without a grace the sweep alarms first."""
    src = Config.from_dict(
        {**CFG, "sources": [{**CFG["sources"][0], "expect_hours": "9-19", "expect_days": "1-5"}]}
    ).sources["watch-due"]
    assert src.expected_now(_at(1, 9, 0)) is False, "the window just opened; give it one interval"
    assert src.expected_now(_at(1, 9, 19)) is False, "1200s expected; 19 minutes is not yet one interval"
    assert src.expected_now(_at(1, 9, 21)) is True, "past one interval into the window, silence counts"


def test_no_schedule_means_always_expected(cfg) -> None:
    """The default, and the behaviour every existing door keeps."""
    assert cfg.sources["watch-due"].expected_now(_at(6, 3, 0)) is True


# ── the real work deployment: a quiet round must keep its door alive ────────
WORK_YAML = Path(__file__).resolve().parents[2] / "deploy" / "work.yaml"


def _work_config(monkeypatch) -> Config:
    """deploy/work.yaml itself, loaded the way scripts/assert_topology.py loads
    it: every ${NAME} it references seeded with a placeholder, and the timer's
    schedule left open so the door is always expected."""
    text = WORK_YAML.read_text(encoding="utf-8")
    for name in set(re.findall(r"\$\{([A-Z0-9_]+)", text)):
        monkeypatch.setenv(name, "https://placeholder.invalid/")
    monkeypatch.setenv("WATCH_HOURS", "")
    monkeypatch.setenv("WATCH_DAYS", "")
    return Config.from_dict(yaml.safe_load(text))


async def test_a_quiet_round_keeps_the_work_door_alive_without_funding_a_run(store, monkeypatch) -> None:
    """38 absence alarms in 7.7 days on the work deployment, 37 of them 25
    minutes after the last round that REACHED the pipe (2026-09-23): the timer
    skipped quiet rounds by posting nothing, and `watch-due` was built to alarm
    on exactly that silence. A quiet round now posts a heartbeat. This pins the
    two halves on the shipped config: the heartbeat is RECORDED (so the absence
    check, which reads the newest event on the door, sees the clock alive) and
    DROPPED before any route (so it never funds a watcher run)."""
    cfg = _work_config(monkeypatch)
    door = cfg.sources["watch-due"]
    t0 = 1_790_000_000.0

    real = await handle_hook(
        store, cfg, door, {"title": "watch", "message": "brief", "state": "alerting", "env": "prod"}, now=t0
    )
    assert real["outcome"] == "routed" and "to-watcher" in real["channels"], "a real round still goes to the watcher"

    for n in (1, 2):
        beat = await handle_hook(
            store,
            cfg,
            door,
            {"title": "watch: quiet round", "message": "quiet", "state": "ok", "env": "prod", "beat": "yes"},
            now=t0 + 1200 * n,
        )
        assert beat["outcome"] == "skipped" and beat["skip_code"] == "quiet_round", beat
        assert not beat.get("channels"), "a heartbeat reaches no channel"

    last = await store.last_event_at("watch-due")
    assert last == t0 + 2400, "heartbeats are events on the door"
    check_at = t0 + 3000
    assert check_at - last <= door.expect_every_seconds, "alive: no alarm"
    assert check_at - t0 > door.expect_every_seconds, "without the heartbeats this was the false alarm"
