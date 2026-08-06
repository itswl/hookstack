"""The judgement and its cost policy — the only thing this service is for."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import httpx
import pytest

from hookjudge.contract import ROUTE_AI, ROUTE_RECOVERY, ROUTE_REUSE, ROUTE_RULE, Incoming, Outgoing
from hookjudge.judge import ai_verdict, reuse_verdict, rule_verdict
from hookjudge.settings import Settings


def settings(**overrides: Any) -> Settings:
    return replace(
        Settings(
            db_path=":memory:",
            ingest_secret="",
            read_token="",
            max_body_bytes=262144,
            return_url="",
            return_secret="",
            return_max_attempts=6,
            worker_interval_seconds=0.01,
            reuse_window_seconds=3600,
            retention_days=30,
            ai_base_url="https://ai.example/v1",
            ai_api_key="k",
            ai_model="test-model",
            ai_timeout_seconds=5.0,
            ai_body_limit=4000,
            ai_price_in_per_1k=0.001,
            ai_price_out_per_1k=0.002,
        ),
        **overrides,
    )


def event(title: str = "充值金额单次超500报警", body: str = "用户 42 充值 920 元", **kw: Any) -> Incoming:
    return Incoming.parse(
        {
            "meta": {"source": kw.pop("source", "grafana"), "correlation_id": kw.pop("correlation_id", "hr-86")},
            "event": {
                "title": title,
                "body": body,
                "level": kw.pop("level", "high"),
                "fields": kw.pop("fields", {}),
            },
            "raw": kw.pop("raw", {}),
        },
        now=1000.0,
    )


class _Response:
    """A str payload means a response body that is not JSON at all."""

    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload
        self._decodable = not isinstance(payload, str)
        self.text = json.dumps(payload) if self._decodable else payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=None)  # type: ignore[arg-type]

    def json(self) -> Any:
        if not self._decodable:
            # The type httpx really raises. A fake that fails differently from
            # the real client tests a failure mode that cannot happen.
            raise json.JSONDecodeError("not json", self.text, 0)
        return self._payload


class FakeAI:
    """Stands in for an OpenAI-compatible endpoint."""

    def __init__(self, response: Any = None, raises: Exception | None = None) -> None:
        self.response = response
        self.raises = raises
        self.calls: list[dict[str, Any]] = []

    async def post(self, url: str, **kwargs: Any) -> _Response:
        self.calls.append({"url": url, **kwargs})
        if self.raises is not None:
            raise self.raises
        return _Response(200, self.response)


def _completion(content: str, *, tokens_in: int = 900, tokens_out: int = 120) -> dict[str, Any]:
    return {
        "model": "test-model",
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": tokens_in, "completion_tokens": tokens_out},
    }


# ── the model path ───────────────────────────────────────────────────────────


async def test_ai_verdict_reads_the_model_and_prices_it():
    client = FakeAI(
        _completion(
            json.dumps(
                {
                    "summary": "9 分钟内 3 次大额充值,涉及两个用户",
                    "importance": "high",
                    "event_type": "business",
                    "impact_scope": "仅触发通知,未见服务影响",
                },
                ensure_ascii=False,
            )
        )
    )
    verdict = await ai_verdict(client, settings(), event())

    assert verdict.route == ROUTE_AI
    assert verdict.summary.startswith("9 分钟内")
    assert verdict.importance == "high" and verdict.event_type == "business"
    # 900 in @ .001/1k + 120 out @ .002/1k
    assert verdict.cost == pytest.approx(0.00114)
    assert verdict.degraded_reason == ""

    sent = client.calls[0]
    assert sent["url"] == "https://ai.example/v1/chat/completions"
    assert sent["headers"]["authorization"] == "Bearer k"
    prompt = sent["json"]["messages"][0]["content"]
    assert "中文" in prompt, "the room speaks Chinese; the answer must too"


async def test_json_wrapped_in_prose_or_fences_is_still_read():
    """Models add fences and preamble more often than anyone admits."""
    fenced = '这是分析结果:\n```json\n{"summary":"磁盘将满","importance":"medium"}\n```\n希望有帮助'
    verdict = await ai_verdict(FakeAI(_completion(fenced)), settings(), event())
    assert verdict.route == ROUTE_AI and verdict.summary == "磁盘将满"


@pytest.mark.parametrize(
    "client,reason",
    [
        (FakeAI(raises=httpx.ConnectError("down")), "AI 调用失败"),
        (FakeAI(_completion("完全不是 JSON 的一段话")), "AI 返回无法解析"),
        (FakeAI(_completion(json.dumps({"importance": "high"}))), "AI 返回无法解析"),
    ],
)
async def test_every_model_failure_lands_on_the_rule_floor_and_says_so(client: FakeAI, reason: str):
    """A downgraded judgement that hides its downgrade is worse than none."""
    verdict = await ai_verdict(client, settings(), event())
    assert verdict.route == ROUTE_RULE
    assert reason in verdict.degraded_reason
    assert verdict.summary, "a floor still answers"


async def test_unconfigured_ai_does_not_pretend():
    verdict = await ai_verdict(FakeAI(), settings(ai_api_key=""), event())
    assert verdict.route == ROUTE_RULE and verdict.degraded_reason == "AI 未配置"


async def test_an_unusable_importance_is_normalized_not_trusted():
    verdict = await ai_verdict(
        FakeAI(_completion(json.dumps({"summary": "s", "importance": "VERY BAD"}))), settings(), event()
    )
    assert verdict.importance == "medium", "an invented scale falls back to the middle"


# ── the rule floor ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "title,expected",
    [
        ("充值金额单次超500报警", "high"),
        ("用户提现异常", "high"),
        ("[测试] 请忽略这条", "low"),
        ("staging deploy finished", "low"),
        ("磁盘使用率 78%", "medium"),
    ],
)
def test_rule_floor_is_crude_but_defensible(title: str, expected: str):
    assert rule_verdict(event(title=title, body="", level="")).importance == expected


def test_rule_floor_infers_a_type_when_it_can():
    # body="" on purpose: the default fixture body mentions 充值, and leaving
    # it in makes every case look like business (it did).
    assert rule_verdict(event(title="支付网关超时", body="")).event_type == "business"
    assert rule_verdict(event(title="k8s 节点磁盘不足", body="")).event_type == "infrastructure"
    assert rule_verdict(event(title="检测到越权访问", body="")).event_type == "security"
    # And the body counts too — type is about the whole alert, not its title.
    assert rule_verdict(event(title="告警", body="证书 7 天后过期")).event_type == "infrastructure"


# ── reuse and recovery ───────────────────────────────────────────────────────


def test_reuse_serves_the_prior_answer_without_contradicting_it():
    prior = {"summary": "原始判断", "importance": "high", "event_type": "business", "impact_scope": "x"}
    again = reuse_verdict(prior, recovery=False)
    assert again.route == ROUTE_REUSE and again.summary == "原始判断" and again.cost == 0

    ended = reuse_verdict(prior, recovery=True)
    assert ended.route == ROUTE_RECOVERY
    assert ended.importance == "high", "a recovery keeps the importance its firing had"
    assert ended.summary == "原始判断", "a recovery that disagrees with its firing reads as a second incident"


@pytest.mark.parametrize(
    "title,expected",
    [
        ("[RESOLVED] 充值金额单次超500报警", True),
        ("[FIRING:1] 充值金额单次超500报警", False),
        ("磁盘告警已恢复", True),
        ("okhttp 连接超时", False),
        ("status: OK", True),
    ],
)
def test_recovery_is_read_not_asked(title: str, expected: bool):
    """Whether the condition ENDED is a fact about the alert, so it is never
    put to the model — and "ok" only counts as a standalone word."""
    assert event(title=title, body="").is_recovery is expected


# ── identity ─────────────────────────────────────────────────────────────────


def test_identity_ignores_the_per_occurrence_noise():
    """If identity included timestamps, every alert would be unique — which is
    the same as having no reuse at all."""
    first = event(fields={"project": "alarm-prod", "timestamp": "10:00:01", "id": "evt-1"})
    second = event(fields={"project": "alarm-prod", "timestamp": "10:04:37", "id": "evt-9"})
    assert first.identity == second.identity

    other = event(fields={"project": "alarm-staging"})
    assert other.identity != first.identity


# ── the outgoing shape ───────────────────────────────────────────────────────


def test_the_result_carries_judgement_and_no_presentation():
    out = Outgoing(
        incoming=event(fields={"project": "alarm-prod", "env": "prod"}),
        verdict=rule_verdict(event()),
    ).payload()

    assert set(out) == {"meta", "analysis", "identity", "links"}
    assert out["meta"]["brain"] == "hookjudge"
    assert out["meta"]["correlation_id"] == "hr-86", "the pipe can gather this under the original"
    assert out["analysis"]["summary"]
    assert out["identity"] == {"project": "alarm-prod", "env": "prod"}
    blob = json.dumps(out, ensure_ascii=False)
    for presentation in ("card", "msg_type", "markdown", "template", "color"):
        assert presentation not in blob, f"{presentation} is the pipe's business, not ours"


def test_the_pipes_real_normalized_wire_shape_parses():
    """These are the exact bytes hookrelay's `payload: normalized` generic
    channel puts on the wire — flat, not wrapped, and with no correlation id
    in the body.

    Reading only the wrapped {meta, event} envelope parsed every one of these
    into empty strings. That is not a blank verdict but a wrong one: identity
    collapses to the same constant for every alert, so event two onward reuse
    event one's judgement forever, and the resulting near-zero paid ratio
    looks like excellent cost savings rather than a broken parser.
    """
    wire = {
        "body": "用户 42 充值 920 元",
        "event_id": 86,
        "fields": {"env": "prod"},
        "level": "high",
        "received_at": 1786000000.0,
        "source": "grafana",
        "title": "充值金额单次超500报警",
    }
    parsed = Incoming.parse(wire, now=1.0)
    assert parsed.source == "grafana"
    assert parsed.title == "充值金额单次超500报警"
    assert parsed.body == "用户 42 充值 920 元"
    assert parsed.level == "high"
    assert parsed.fields == {"env": "prod"}
    assert parsed.received_at == 1786000000.0
    assert parsed.identity == "grafana|充值金额单次超500报警|env=prod"

    other = Incoming.parse({**wire, "event_id": 87, "title": "磁盘使用率 91%"}, now=1.0)
    assert other.identity != parsed.identity, "two conditions must not share one identity"


def test_correlation_id_comes_from_the_transport_when_the_body_omits_it():
    """The flat shape carries no correlation id; hookrelay puts it in a header.
    Without reading it there, the pipe cannot group the judgement with the
    event that caused it."""
    wire = {"source": "grafana", "title": "t", "event_id": 86}
    assert Incoming.parse(wire, now=1.0, correlation_id="hr-86").correlation_id == "hr-86"

    # Header stripped by a proxy: fall back to the pipe's own convention.
    assert Incoming.parse(wire, now=1.0).correlation_id == "hr-86"

    # An explicit body value still wins over both.
    wrapped = {"meta": {"source": "s", "correlation_id": "hr-1"}, "event": {"title": "t"}}
    assert Incoming.parse(wrapped, now=1.0, correlation_id="hr-999").correlation_id == "hr-1"


def test_the_wrapped_envelope_still_parses():
    """The documented shape must keep working — a brain-agnostic contract is
    the point, and the flat shape is one pipe's dialect of it."""
    parsed = Incoming.parse(
        {
            "meta": {"source": "alertmanager", "correlation_id": "hr-9", "received_at": 5.0},
            "event": {"title": "t", "body": "b", "level": "HIGH", "fields": {"env": "prod"}},
            "raw": {"state": "firing"},
        },
        now=1.0,
    )
    assert parsed.source == "alertmanager" and parsed.correlation_id == "hr-9"
    assert parsed.level == "high" and parsed.raw == {"state": "firing"}
    assert parsed.received_at == 5.0
