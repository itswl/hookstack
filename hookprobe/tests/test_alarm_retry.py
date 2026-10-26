"""An alarm is one message with nothing behind it that resends it.

A name that does not resolve for a moment is not the channel refusing; an HTTP
error is. The first is tried again in place, the second is not.
"""

from __future__ import annotations

import asyncio
import socket
import urllib.error

from hookprobe import notify
from hookprobe.runs import RunStore
from tests.helpers import make_settings


def _delivery(tmp_path, monkeypatch, outcomes):
    monkeypatch.setattr(notify, "ALARM_RETRY_DELAY_SECONDS", 0)
    settings = make_settings(tmp_path, workdir=tmp_path, alarm_url="https://alarm.invalid/hook")
    delivery = notify.ReturnDelivery(settings, RunStore(tmp_path / "results"))
    tries: list[bytes] = []

    def post(body: bytes) -> None:
        tries.append(body)
        outcome = outcomes[min(len(tries), len(outcomes)) - 1]
        if outcome is not None:
            raise outcome

    monkeypatch.setattr(delivery, "_post_alarm", post)
    return delivery, tries


def _unresolved() -> urllib.error.URLError:
    return urllib.error.URLError(socket.gaierror(-2, "Name or service not known"))


def test_a_name_that_did_not_resolve_is_tried_again_in_place(tmp_path, monkeypatch) -> None:
    delivery, tries = _delivery(tmp_path, monkeypatch, [_unresolved(), _unresolved(), None])
    assert asyncio.run(delivery.alarm("node down")) is True
    assert len(tries) == 3


def test_the_channel_refusing_is_not_asked_again(tmp_path, monkeypatch) -> None:
    refused = urllib.error.HTTPError("https://alarm.invalid/hook", 400, "Bad Request", None, None)
    delivery, tries = _delivery(tmp_path, monkeypatch, [refused])
    assert asyncio.run(delivery.alarm("node down")) is False
    assert len(tries) == 1


def test_after_its_retries_it_gives_up_and_the_next_alarm_still_tries(tmp_path, monkeypatch) -> None:
    delivery, tries = _delivery(tmp_path, monkeypatch, [_unresolved()])
    assert asyncio.run(delivery.alarm("node down")) is False
    assert len(tries) == 3
    assert asyncio.run(delivery.alarm("still down")) is False
    assert len(tries) == 6, "a failed alarm does not open the quiet window"
