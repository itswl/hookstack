"""Why a model call failed, at the granularity that changes the response.

Lumping every failure into "degraded → rule floor" was the gap: a dead key and
a rate limit fell to the same opaque reason and neither said anything, so a bad
key silently judged every alert by keywords until somebody read the ledger. This
pins the classifier and the one distinction that has teeth — must-act vs
transient — because that is what decides whether an operator is told.
"""

from __future__ import annotations

import httpx
import pytest

from hookjudge import judge
from hookjudge.judge import PROVIDER_ERROR_MUST_ACT, classify_provider_error


@pytest.mark.parametrize(
    "status,body,expected",
    [
        (401, "unauthorized", judge.PROVIDER_ERROR_AUTH),
        (403, "forbidden", judge.PROVIDER_ERROR_AUTH),
        (403, "账户余额不足", judge.PROVIDER_ERROR_BILLING),
        (402, "", judge.PROVIDER_ERROR_BILLING),
        (200, "insufficient credit balance", judge.PROVIDER_ERROR_BILLING),
        (429, "rate limit exceeded, please retry", judge.PROVIDER_ERROR_RATE_LIMIT),
        (429, "you exceeded your current quota", judge.PROVIDER_ERROR_QUOTA),
        (400, "maximum context length is 128000 tokens", judge.PROVIDER_ERROR_CONTEXT),
        (500, "internal server error", judge.PROVIDER_ERROR_PROVIDER),
        (503, "upstream unavailable", judge.PROVIDER_ERROR_PROVIDER),
    ],
)
def test_the_status_and_body_place_a_failure(status, body, expected):
    assert classify_provider_error(status, body) == expected


def test_a_transport_error_never_reached_the_provider():
    exc = httpx.ConnectError("connection refused")
    assert classify_provider_error(None, "", exc) == judge.PROVIDER_ERROR_TRANSPORT


def test_the_must_act_set_is_the_line_that_alarms():
    """auth/billing/quota do not pass on their own; the operator must act, and
    until they do every verdict is the rule floor. rate_limit and provider
    blips pass, so they must NOT be in the alarming set — an alarm that cries on
    a transient gets muted."""
    assert judge.PROVIDER_ERROR_AUTH in PROVIDER_ERROR_MUST_ACT
    assert judge.PROVIDER_ERROR_BILLING in PROVIDER_ERROR_MUST_ACT
    assert judge.PROVIDER_ERROR_QUOTA in PROVIDER_ERROR_MUST_ACT
    assert judge.PROVIDER_ERROR_RATE_LIMIT not in PROVIDER_ERROR_MUST_ACT
    assert judge.PROVIDER_ERROR_PROVIDER not in PROVIDER_ERROR_MUST_ACT
    assert judge.PROVIDER_ERROR_TRANSPORT not in PROVIDER_ERROR_MUST_ACT


async def test_a_dead_key_degrades_with_its_category_named(monkeypatch):
    """The whole point, end to end: a 401 falls to the rule floor like before,
    but now the verdict CARRIES the category, so the caller can alarm instead of
    letting a week of keyword verdicts pass as if the model had answered."""
    import time

    from hookjudge.contract import Incoming
    from tests.test_judge import settings as _judge_settings

    def settings():
        return _judge_settings(ai_api_key="k", ai_base_url="https://ai.example/v1")

    class _Resp:
        status_code = 401
        text = "unauthorized"

        def raise_for_status(self):
            raise httpx.HTTPStatusError("401", request=None, response=None)

        def json(self):
            return {}

    class _Client:
        async def post(self, *a, **k):
            return _Resp()

    event = Incoming.parse(
        {"source": "ww", "title": "payment down", "body": "x", "level": "high", "fields": {}}, now=time.time()
    )
    verdict = await judge.ai_verdict(_Client(), settings(), event)
    assert verdict.route == "rule", "it still falls to the floor"
    assert verdict.degraded_category == judge.PROVIDER_ERROR_AUTH
    assert "auth" in verdict.degraded_reason
