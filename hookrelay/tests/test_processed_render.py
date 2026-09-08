"""One judgement, one model — the dirty work lifted off the brain AND off the pipe.

A brain that renders Feishu cards must know Feishu's card schema, its colour
names and its markdown dialect; then WeCom's; then DingTalk's. First that work
moved into the pipe; now the pipe turns a RESULT into a card MODEL and hands it
to whoever knows a platform (a bridge, docs/bridge-protocol.md; a plugin for
markdown webhooks). Here the brain sends a result and the pipe models it; the
dialects are asserted where they live (deploy/lark-bridge/tests,
test_chat_markdown_plugins.py).
"""

from __future__ import annotations

import json

import pytest

from hookrelay.channels import build_request
from hookrelay.config import Channel
from hookrelay.processed import Processed

RESULT = {
    "meta": {
        "alert_name": "Single top-up over 500",
        "source": "grafana",
        "importance": "high",
        "brain": "brain-full",
        "correlation_id": "hr-86",
        "timestamp": "2026-08-07 10:32:50",
        "is_recovery": False,
    },
    "analysis": {
        "summary": "three large top-ups in nine minutes across two accounts",
        "event_type": "business",
        "impact_scope": "limited to the notification itself; no direct service impact observed",
    },
    "identity": {"project": "demo-alarm", "env": "prod", "rule": "Single top-up over 500"},
    "links": [{"text": "Large top-up runbook", "url": "https://kb.example/runbook/42"}],
    "actions": [{"text": "Acknowledge", "value": {"signed": "opaque-token"}, "style": "primary"}],
}


def _channel(kind: str, **options) -> Channel:
    return Channel(
        name=f"{kind}-out",
        type=kind,
        url=f"https://{kind}.example/hook",
        options={"payload": "processed", **options},
    )


def _message(result: dict = RESULT) -> dict:
    # The relay's own normalized view is thin here on purpose: in this posture
    # the door reads only enough to route and to keep the ledger legible; the
    # RENDERING input is the brain's structured result in `payload`.
    return {
        "event_id": 90,
        "source": "ww-notify",
        "title": result["meta"]["alert_name"],
        "body": "",
        "level": result["meta"]["importance"],
        "fields": {},
        "received_at": 1000.0,
        "payload": result,
    }


def _model(result: dict = RESULT) -> dict:
    _url, body, _headers = build_request(_channel("bridge"), _message(result), now=0.0)
    return json.loads(body)["card"]


def test_the_model_carries_every_block_as_facts():
    card = _model()
    assert card["tone"] == "high", "high importance is the state; the colour it earns is the renderer's"
    assert card["title"] == "📡 Single top-up over 500"
    assert card["lead"] == "🔴 HIGH"
    assert card["summary"] == "three large top-ups in nine minutes across two accounts"
    assert card["crumb"] == "demo-alarm · prod · Single top-up over 500", "identity as a breadcrumb, not a label grid"
    assert card["impact"].startswith("limited to the notification itself")
    assert card["links"] == [{"text": "Large top-up runbook", "url": "https://kb.example/runbook/42"}], (
        "the runbook travels WITH the alert"
    )
    assert card["footer"] == "grafana · business · 2026-08-07 10:32:50"
    # The action's value stays opaque — signing identity is the brain's judgement, not the pipe's formatting.
    assert card["actions"] == [{"text": "Acknowledge", "style": "primary", "value": {"signed": "opaque-token"}}]
    for key in ("title", "lead", "summary", "crumb", "impact", "footer"):
        assert "\\" not in card[key], f"{key} is plain text — escaping is the renderer's, for its own dialect"


def test_a_recovery_names_its_state_and_a_reminder_says_still_open():
    recovered = json.loads(json.dumps(RESULT))
    recovered["meta"]["is_recovery"] = True
    card = _model(recovered)
    assert card["tone"] == "recovery" and card["title"].startswith("✅ Resolved")
    reminder = json.loads(json.dumps(RESULT))
    reminder["meta"]["is_periodic_reminder"] = True
    assert _model(reminder)["title"].startswith("🔁 Still open")


def test_generic_receives_the_structure_itself_signed():
    """A machine consumer wants the judgement, not a rendering of it."""
    channel = Channel(
        name="archive",
        type="generic",
        url="https://archive.example/in",
        secret="s3",
        options={"payload": "processed"},
    )
    _url, body, headers = build_request(channel, _message(), now=0.0)
    assert isinstance(body, bytes)
    assert json.loads(body.decode()) == RESULT
    import hashlib
    import hmac as hmac_mod

    assert headers["X-Hook-Signature"] == hmac_mod.new(b"s3", body, hashlib.sha256).hexdigest()


def test_one_judgement_reaches_people_and_machines_unchanged_in_meaning():
    """The point of the split: the same result, as a model for a person's chat
    and as itself for an archive, each carrying the summary and the runbook."""
    for kind in ("bridge", "generic"):
        _url, payload, _headers = build_request(_channel(kind), _message(), now=0.0)
        text = payload.decode()
        assert "three large top-ups in nine minutes" in text, f"{kind} lost the summary"
        assert "kb.example/runbook/42" in text, f"{kind} lost the runbook"


async def test_a_non_object_payload_fails_into_the_ledger():
    """Misconfiguration surfaces as a named delivery failure, never as an
    empty message delivered to a chat group."""
    from hookrelay import channels as channels_mod

    message = _message()
    message["payload"] = "not an object"
    with pytest.raises(TypeError, match="not an object"):
        build_request(_channel("bridge"), message, now=0.0)
    ok, detail, body, _ = await channels_mod.send(object(), _channel("bridge"), message)
    assert ok is False and "build:" in detail
    assert body is None  # nothing was built, so there are no bytes to keep


@pytest.mark.parametrize("bad", ["a string", ["a", "list"], 7])
def test_a_non_object_meta_is_not_a_poison_pill(bad: object):
    """A processed payload whose meta/analysis/identity is the wrong type used to
    reach an accessor as an AttributeError during build, escape send()'s narrow
    except, never dead-letter, and be retried every tick forever — head-of-line
    blocking its channel. Coercion to {} means build models (from whatever is
    left) instead of raising."""
    message = _message()
    payload = dict(message["payload"])
    payload["meta"] = bad
    payload["analysis"] = bad
    payload["identity"] = bad
    message = {**message, "payload": payload}
    _url, built, _headers = build_request(_channel("bridge"), message, now=0.0)
    assert json.loads(built)["card"]["title"] == "📡 Alert"  # a model from the defaults, not an exception


def test_missing_optional_blocks_are_absent_not_empty():
    """Brains differ: a lite brain has no impact analysis and no KB. Its model
    must simply lack those keys — a renderer then draws no empty sections."""
    lean = {
        "meta": {"alert_name": "Disk about to fill", "importance": "medium", "source": "lite"},
        "analysis": {"summary": "s"},
    }
    card = _model(lean)
    assert "impact" not in card and "links" not in card and "actions" not in card and "crumb" not in card
    assert card["summary"] == "s" and card["tone"] == "medium"


def test_the_footer_timestamp_reads_as_a_clock_not_an_epoch():
    """A brain sends an epoch; a person reads a clock.

    meta.timestamp went into the card as the float it arrived as, so a real
    end-to-end run ended its card with "· 1786037727.669673". Same format as
    the status page so one alert reads the same in both places; a brain that
    already formatted its own string keeps it; milliseconds are absorbed."""
    import re

    def footer(stamp: object) -> str:
        return Processed({"meta": {"alert_name": "x", "source": "s", "timestamp": stamp}}).footer()

    assert re.fullmatch(r"s · \d\d-\d\d \d\d:\d\d:\d\d", footer(1786037727.669673))
    assert footer(1786037727669) == footer(1786037727.669673), "milliseconds are the same instant"
    assert footer("2026-08-07 10:32:50") == "s · 2026-08-07 10:32:50"
    assert footer(None) == "s" and footer("  ") == "s"
