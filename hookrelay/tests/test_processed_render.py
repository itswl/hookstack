"""One judgement, four dialects — the dirty work lifted off the brain.

A brain that renders Feishu cards must know Feishu's card schema, its colour
names and its markdown dialect; then WeCom's; then DingTalk's. That is exactly
what belongs in the pipe. Here the brain sends a RESULT and each channel type
dresses it, so the same judgement reaches four downstreams in their own
language without the brain knowing any of them.
"""

from __future__ import annotations

import json

import pytest

from hookrelay.channels import build_request
from hookrelay.config import Channel
from hookrelay.processed import Processed

RESULT = {
    "meta": {
        "alert_name": "示例充值超500告警",
        "source": "grafana",
        "importance": "high",
        "brain": "brain-full",
        "correlation_id": "hr-86",
        "timestamp": "2026-08-07 10:32:50",
        "is_recovery": False,
    },
    "analysis": {
        "summary": "9 分钟内连续 3 次大额充值,涉及两个用户",
        "event_type": "business",
        "impact_scope": "影响范围限于触发告警通知,未观察到对服务的直接影响",
    },
    "identity": {"project": "demo-alarm", "env": "prod", "rule": "示例充值超500告警"},
    "links": [{"text": "大额充值处置预案", "url": "https://kb.example/runbook/42"}],
    "actions": [{"text": "确认接手", "value": {"signed": "opaque-token"}, "style": "primary"}],
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


def test_feishu_gets_a_card_with_every_block_and_the_buttons():
    _url, payload, _headers = build_request(_channel("feishu"), _message(), now=0.0)

    assert payload["msg_type"] == "interactive"
    header = payload["card"]["header"]
    assert header["template"] == "red", "high importance earns red"
    assert "示例充值超500告警" in header["title"]["content"]

    blocks = json.dumps(payload["card"]["elements"], ensure_ascii=False)
    assert "9 分钟内连续 3 次大额充值" in blocks, "the headline carries the summary"
    assert "demo-alarm · prod" in blocks, "identity reads as a breadcrumb, not a label grid"
    assert "**影响**" in blocks and "未观察到对服务的直接影响" in blocks
    assert "大额充值处置预案" in blocks, "the runbook arrives WITH the alert"
    assert "grafana · business" in blocks, "footer is de-emphasised metadata"

    # Interactive callbacks are Feishu-only, and the value stays opaque —
    # signing identity is the brain's judgement, not the pipe's formatting.
    action = next(e for e in payload["card"]["elements"] if e.get("tag") == "action")
    assert action["actions"][0]["text"]["content"] == "确认接手"
    assert action["actions"][0]["value"] == {"signed": "opaque-token"}


def test_a_recovery_is_green_and_says_so_first():
    """A recovery card wearing a red header contradicts its own text."""
    recovered = json.loads(json.dumps(RESULT))
    recovered["meta"]["is_recovery"] = True
    _url, payload, _headers = build_request(_channel("feishu"), _message(recovered), now=0.0)

    assert payload["card"]["header"]["template"] == "green"
    assert payload["card"]["header"]["title"]["content"].startswith("✅ 已恢复")


def test_periodic_reminder_is_labelled_in_the_headline():
    reminder = json.loads(json.dumps(RESULT))
    reminder["meta"]["is_periodic_reminder"] = True
    _url, payload, _headers = build_request(_channel("feishu"), _message(reminder), now=0.0)
    assert payload["card"]["header"]["title"]["content"].startswith("🔁 未处理提醒")


def test_dingtalk_and_wecom_get_their_own_markdown_without_dead_buttons():
    _url, ding, _h = build_request(_channel("dingtalk"), _message(), now=1700000000.0)
    assert ding["msgtype"] == "markdown"
    text = ding["markdown"]["text"]
    assert text.startswith("### 📡 示例充值超500告警"), "DingTalk wants a heading"
    assert "🔴 高" in text and "9 分钟内连续" in text
    assert "大额充值处置预案" in text, "links survive where buttons cannot"
    assert "signed" not in text, "a button with no callback channel is worse than none"

    _url, wecom, _h = build_request(_channel("wecom"), _message(), now=0.0)
    content = wecom["markdown"]["content"]
    assert content.startswith("**📡 示例充值超500告警**"), "WeCom renders bold, not #"
    assert "**影响**" in content


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


def test_one_judgement_reaches_four_dialects_unchanged_in_meaning():
    """The point of the split, asserted as one statement: the same result, four
    formats, each carrying the summary and the runbook link."""
    rendered = {}
    for kind in ("feishu", "dingtalk", "wecom", "generic"):
        _url, payload, _headers = build_request(_channel(kind), _message(), now=0.0)
        rendered[kind] = payload.decode() if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False)

    for kind, text in rendered.items():
        assert "9 分钟内连续 3 次大额充值" in text, f"{kind} lost the summary"
        assert "kb.example/runbook/42" in text, f"{kind} lost the runbook"
    # And they are genuinely different renderings, not one shape reused.
    assert len({rendered["feishu"], rendered["dingtalk"], rendered["wecom"]}) == 3


async def test_a_non_object_payload_fails_into_the_ledger():
    """Misconfiguration surfaces as a named delivery failure, never as an
    empty message delivered to a chat group."""
    from hookrelay import channels as channels_mod

    message = _message()
    message["payload"] = "not an object"
    with pytest.raises(TypeError, match="not an object"):
        build_request(_channel("feishu"), message, now=0.0)

    ok, detail, body = await channels_mod.send(object(), _channel("feishu"), message)
    assert ok is False and "build:" in detail
    assert body is None  # nothing was built, so there are no bytes to keep


def test_missing_optional_blocks_render_without_holes():
    """Brains differ: a lite brain has no impact analysis and no KB. Its result must
    still produce a clean card, not one with empty sections."""
    lean = {"meta": {"alert_name": "磁盘将满", "importance": "medium", "source": "lite"}, "analysis": {"summary": "s"}}
    _url, payload, _headers = build_request(_channel("feishu"), _message(lean), now=0.0)
    blocks = json.dumps(payload["card"]["elements"], ensure_ascii=False)
    assert "影响" not in blocks and "相关文档" not in blocks
    assert "s" in blocks and payload["card"]["header"]["template"] == "orange"


def test_the_footer_timestamp_reads_as_a_clock_not_an_epoch():
    """A brain sends an epoch; a person reads a clock.

    meta.timestamp went into the card as the float it arrived as, so a real
    end-to-end run ended its Feishu card with "· 1786037727.669673". Same
    format as the status page so one alert reads the same in both places.
    """
    import re
    import time

    epoch = 1786037727.669673
    card = Processed({"meta": {"source": "inbound", "timestamp": epoch}, "analysis": {"event_type": "business"}})
    footer = card.footer()

    assert "1786037727" not in footer, "an epoch is not a time a person can read"
    assert re.search(r"\d{2}-\d{2} \d{2}:\d{2}:\d{2}", footer), footer
    assert footer.endswith(time.strftime("%m-%d %H:%M:%S", time.localtime(epoch)))
    assert footer.startswith("inbound · business · ")


def test_the_footer_tolerates_what_brains_actually_send():
    """Milliseconds, an already-formatted string, and nothing at all."""
    ms = Processed({"meta": {"timestamp": 1786037727669}}).footer()
    sec = Processed({"meta": {"timestamp": 1786037727}}).footer()
    assert ms == sec, "milliseconds must not render as a date in the year 58000"

    # A brain that formatted its own string keeps it — it knows its own room.
    assert Processed({"meta": {"timestamp": "2026-08-06 17:35"}}).footer() == "2026-08-06 17:35"

    assert Processed({"meta": {"source": "grafana"}}).footer() == "grafana"
    assert Processed({"meta": {"timestamp": ""}}).footer() == ""
    assert Processed({"meta": {"timestamp": None}}).footer() == ""
