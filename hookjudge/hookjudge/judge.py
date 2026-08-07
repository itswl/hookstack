"""The judgement itself — the only thing this service is for.

Four routes, tried in this order, and the order is the cost policy:

  recovery — the condition ENDED. Reuse what its firing was judged to be; a
             recovery is not a new problem to analyse, and re-analysing it
             both costs a call and risks contradicting the original.
  reuse    — the same identity was judged inside the window. Alert storms are
             the same condition restated, so paying per restatement is paying
             for the same answer repeatedly.
  ai       — a model reads it.
  rule     — the model was unavailable, slow, or answered unusably. Keyword
             rules decide, and the verdict says so (degraded_reason), because
             a downgraded judgement that hides its downgrade is worse than a
             missing one.

The prompt is Chinese on purpose: these alerts are Chinese and the summary is
read by Chinese-speaking operators, so the model must answer in the language
of the room. That is a product decision, not display copy.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from hookjudge.contract import ROUTE_AI, ROUTE_RECOVERY, ROUTE_REUSE, ROUTE_RULE, Incoming, Verdict

_SYSTEM_PROMPT = """你是运维告警分析助手。读一条告警,输出严格的 JSON,不要任何解释文字。

字段:
  summary       一句话说清发生了什么(中文,不超过 60 字,写事实不写套话)
  importance    critical / high / medium / low 四选一
  event_type    business / infrastructure / security / deploy / test 之一
  impact_scope  影响范围;判断不了就写"影响范围未知"

判断口径:
  - 只依据告警内容判断,不要猜测未提供的信息
  - 业务金额、支付、账号安全类默认不低于 high
  - 磁盘/内存/CPU 等容量类看阈值紧迫程度
  - 明显的测试、演练、"请忽略"类告警一律 low
"""

_RULE_HIGH = ("充值", "提现", "支付", "订单", "余额", "资金", "安全", "攻击", "泄露", "宕机", "不可用", "down")
_RULE_LOW = ("测试", "请忽略", "演练", "test", "demo", "staging")
_TYPE_HINTS = (
    ("business", ("充值", "提现", "支付", "订单", "余额", "交易")),
    ("security", ("安全", "攻击", "泄露", "入侵", "越权")),
    ("deploy", ("发布", "部署", "deploy", "release", "回滚")),
    ("infrastructure", ("磁盘", "内存", "cpu", "节点", "集群", "证书", "网络", "数据库")),
)


def rule_verdict(event: Incoming, *, degraded_reason: str = "") -> Verdict:
    """Keyword judgement — the floor under every other route.

    Deliberately crude: its job is to be a defensible answer when the model
    cannot speak, not to imitate one. It says so in degraded_reason so nobody
    mistakes it for analysis.
    """
    haystack = f"{event.title} {event.body}".lower()
    if any(word in haystack for word in _RULE_LOW):
        importance = "low"
    elif any(word in haystack for word in _RULE_HIGH):
        importance = "high"
    elif event.level in ("critical", "high", "warning", "medium", "low"):
        importance = {"warning": "medium"}.get(event.level, event.level)
    else:
        importance = "medium"

    event_type = ""
    for name, hints in _TYPE_HINTS:
        if any(hint in haystack for hint in hints):
            event_type = name
            break

    return Verdict(
        summary=event.title or "(无标题告警)",
        importance=importance,
        event_type=event_type,
        impact_scope="影响范围未知(规则判定)",
        route=ROUTE_RULE,
        degraded_reason=degraded_reason,
    ).normalized()


def _extract_json(text: str) -> dict[str, Any] | None:
    """Models wrap JSON in prose or fences more often than anyone admits."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


async def ai_verdict(client: httpx.AsyncClient, settings: Any, event: Incoming) -> Verdict:
    """Ask the model. Any failure returns a rule verdict that SAYS it degraded."""
    if not settings.ai_api_key or not settings.ai_base_url:
        return rule_verdict(event, degraded_reason="AI 未配置")

    context = {
        "source": event.source,
        "title": event.title,
        "body": event.body[: settings.ai_body_limit],
        "level": event.level,
        "fields": event.fields,
    }
    request = {
        "model": settings.ai_model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    try:
        response = await client.post(
            f"{settings.ai_base_url.rstrip('/')}/chat/completions",
            json=request,
            headers={"authorization": f"Bearer {settings.ai_api_key}"},
            timeout=settings.ai_timeout_seconds,
        )
        response.raise_for_status()
        body = response.json()
    except Exception as error:  # noqa: BLE001 — every failure lands on the same floor
        return rule_verdict(event, degraded_reason=f"AI 调用失败: {error.__class__.__name__}")

    choices = body.get("choices") or []
    content = str(((choices[0] if choices else {}).get("message") or {}).get("content") or "")
    parsed = _extract_json(content)
    if not parsed or not str(parsed.get("summary") or "").strip():
        return rule_verdict(event, degraded_reason="AI 返回无法解析")

    usage = body.get("usage") or {}
    tokens_in = int(usage.get("prompt_tokens") or 0)
    tokens_out = int(usage.get("completion_tokens") or 0)
    return Verdict(
        summary=str(parsed.get("summary") or ""),
        importance=str(parsed.get("importance") or ""),
        event_type=str(parsed.get("event_type") or ""),
        impact_scope=str(parsed.get("impact_scope") or ""),
        route=ROUTE_AI,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost=round(
            (tokens_in / 1000) * settings.ai_price_in_per_1k + (tokens_out / 1000) * settings.ai_price_out_per_1k,
            6,
        ),
        model=str(body.get("model") or settings.ai_model),
    ).normalized()


def reuse_verdict(prior: dict[str, Any], *, recovery: bool) -> Verdict:
    """A prior judgement, re-served. Costs nothing and cannot contradict the
    original — which is the point: a recovery that disagrees with its own
    firing alert reads as two unrelated events to whoever is on call."""
    return Verdict(
        summary=str(prior.get("summary") or ""),
        importance=str(prior.get("importance") or "medium"),
        event_type=str(prior.get("event_type") or ""),
        impact_scope=str(prior.get("impact_scope") or ""),
        route=ROUTE_RECOVERY if recovery else ROUTE_REUSE,
        model=str(prior.get("model") or ""),
    ).normalized()
