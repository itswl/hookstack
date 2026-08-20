"""The card's way back: a button press that means something.

A notification card used to be a dead end — the operator could read it and
nothing else, because every useful response lived behind a web board and a
token. These tests pin the round trip: a brain DECLARES which actions its
verdict deserves, the pipe mints signed buttons, and a press comes back through
/card-action to be acted on exactly once.
"""

from __future__ import annotations

import json
import time

import pytest

from hookrelay import actions
from hookrelay.config import Config

PROCESSED = {
    "meta": {"alert_name": "Payment gateway 5xx", "correlation_id": "hr-1", "importance": "critical"},
    "analysis": {"summary": "gateway-2 is failing 8% of calls", "impact_scope": "checkout"},
    "identity": {"env": "prod"},
    "actions": [
        {"kind": "silence", "text": "Silence 1h", "minutes": 60},
        {"kind": "followup", "text": "Ask why", "prompt": "Why do you believe that?"},
        {"kind": "approve", "text": "Approve: restart gateway-2", "ref": "p-1"},
    ],
}


def _cfg_with_actions() -> Config:
    """A pipe that offers silence and followup, but never approve.

    The omission is the point: `approve` runs commands, so it is opt-in per
    deployment and a brain asking for it is a request, not a guarantee.
    """
    return Config.from_dict(
        {
            "sources": [{"name": "judge-notify", "secret": "", "title": "{meta.alert_name}", "body": "{x}"}],
            "channels": [
                {"name": "ops-feishu", "type": "feishu", "url": "https://feishu.example/hook"},
                {"name": "probe-action", "type": "generic", "url": "https://probe.example/hooks/action"},
            ],
            "routes": [{"name": "all", "source": "*", "send_to": ["ops-feishu"]}],
            "card_actions": {
                "silence": {"params": {"minutes": 60}},
                "followup": {"forward_to": "probe-action"},
            },
        }
    )


# ── the token ────────────────────────────────────────────────────────────────


def test_a_token_survives_a_round_trip_and_nothing_else_does() -> None:
    minted = actions.mint("s3cret", kind="silence", event_id=7, correlation_id="hr-7", now=1000.0)
    claims = actions.verify("s3cret", minted, now=1001.0)
    assert claims["k"] == "silence" and claims["e"] == 7 and claims["c"] == "hr-7"

    with pytest.raises(actions.ActionError):
        actions.verify("another-secret", minted, now=1001.0)
    with pytest.raises(actions.ActionError):
        actions.verify("s3cret", minted, now=1000.0 + actions.DEFAULT_TTL_SECONDS + 1)
    with pytest.raises(actions.ActionError):
        actions.verify("s3cret", "not-a-token", now=1001.0)
    # An empty secret closes the door rather than opening it: this token is a
    # bearer credential, so "unconfigured" must never mean "unchecked".
    with pytest.raises(actions.ActionError):
        actions.verify("", minted, now=1001.0)


def test_a_tampered_claim_is_refused_not_read() -> None:
    """The payload is base64, so it is editable by anyone. The signature is what
    makes it worthless to edit — and it is checked before the body is parsed."""
    minted = actions.mint("s3cret", kind="silence", event_id=7, correlation_id="hr-7", now=1000.0)
    encoded, _, signature = minted.rpartition(".")
    forged = actions.mint("s3cret", kind="approve", event_id=7, correlation_id="hr-7", now=1000.0).partition(".")[0]
    with pytest.raises(actions.ActionError):
        actions.verify("s3cret", f"{forged}.{signature}", now=1001.0)


def test_only_configured_kinds_become_buttons() -> None:
    cfg = _cfg_with_actions()
    buttons = actions.offered(
        "s3cret",
        PROCESSED["actions"],  # type: ignore[arg-type]
        {kind: {"params": spec.params} for kind, spec in cfg.card_actions.items()},
        event_id=1,
        correlation_id="hr-1",
        now=1000.0,
    )
    assert [b["text"] for b in buttons] == ["Silence 1h", "Ask why"], "approve was never offered here"
    kinds = {actions.verify("s3cret", b["value"]["hookrelay_action"], now=1001.0)["k"] for b in buttons}
    assert kinds == {"silence", "followup"}


def test_without_a_secret_no_card_carries_a_button() -> None:
    """An unsigned button is a URL anyone in the group chat can press on your
    behalf, so the unconfigured default is no buttons at all."""
    cfg = _cfg_with_actions()
    assert (
        actions.offered(
            "",
            PROCESSED["actions"],  # type: ignore[arg-type]
            {kind: {"params": spec.params} for kind, spec in cfg.card_actions.items()},
            event_id=1,
            correlation_id="hr-1",
            now=1000.0,
        )
        == []
    )


def test_config_refuses_an_action_it_could_never_perform() -> None:
    """Unknown kinds and unroutable forwards fail AT BOOT, like every other name
    in the config: a button that 404s when an operator finally presses it is
    worse than no button."""
    from hookrelay.config import ConfigError

    base = {
        "sources": [{"name": "s", "secret": "", "title": "{t}", "body": "{b}"}],
        "channels": [{"name": "c", "type": "generic", "url": "https://example.invalid/x"}],
        "routes": [{"name": "r", "source": "*", "send_to": ["c"]}],
    }
    with pytest.raises(ConfigError, match="unknown kind"):
        Config.from_dict({**base, "card_actions": {"reboot-everything": {"forward_to": "c"}}})
    with pytest.raises(ConfigError, match="not a configured channel"):
        Config.from_dict({**base, "card_actions": {"followup": {"forward_to": "nowhere"}}})
    with pytest.raises(ConfigError, match="needs forward_to"):
        Config.from_dict({**base, "card_actions": {"followup": {}}})
    # silence is the exception: the pipe owns silences, so it needs no channel.
    assert Config.from_dict({**base, "card_actions": {"silence": {}}}).card_actions["silence"].forward_to == ""


# ── the round trip over HTTP ─────────────────────────────────────────────────


@pytest.fixture
async def action_client(settings, tmp_path):
    """A pipe with card actions enabled, and a probe door to forward them to."""
    import dataclasses

    import httpx as _httpx

    from hookrelay.app import create_app

    wired = dataclasses.replace(settings, action_secret="card-s3cret", db_path=str(tmp_path / "actions.db"))
    app = create_app(settings=wired, cfg=_cfg_with_actions())
    async with (
        _httpx.ASGITransport(app=app) as transport,
        app.router.lifespan_context(app),
        _httpx.AsyncClient(transport=transport, base_url="http://t") as client,
    ):
        client.app = app  # type: ignore[attr-defined]
        yield client


def _mint_now(kind: str, **kwargs) -> str:
    """The endpoint reads the real clock, so a token for it must be minted
    against the real clock too — a fixed 1000.0 is expired by decades."""
    return actions.mint("card-s3cret", kind=kind, now=time.time(), **kwargs)


async def _press(client, token: str, **extra):
    """What an IM platform POSTs when a button is pressed."""
    body = {"action": {"value": {"hookrelay_action": token}}, **extra}
    return await client.post("/card-action", content=json.dumps(body).encode())


async def test_a_press_silences_the_source_once(action_client):
    """The whole point, end to end: a human reading a card in chat quiets the
    alert without opening a board, and a second press changes nothing."""
    store = action_client.app.state.store
    token = _mint_now("silence", event_id=1, correlation_id="hr-1", params={"minutes": 30})

    first = await _press(action_client, token, open_id="ou_abc")
    assert first.status_code == 200
    assert first.json()["outcome"].startswith("silenced")
    assert await store.active_silence("judge-notify", time.time()) is not None, "the alert is actually quiet now"

    # Pressed twice, or the platform retried: the ledger refuses by identity.
    again = await _press(action_client, token, open_id="ou_abc")
    assert again.status_code == 200 and again.json()["outcome"] == "already_done"

    pressed = await store.recent_actions()
    assert len(pressed) == 1, "one row per press that was actually honoured"
    assert pressed[0]["kind"] == "silence" and pressed[0]["actor"] == "ou_abc"


async def test_a_forwarded_press_rides_the_outbox(action_client):
    """followup is not the pipe's job, so it becomes an event and a delivery to
    the configured channel — inheriting the retry, the rate limit and the ledger
    row instead of growing a second delivery mechanism."""
    store = action_client.app.state.store
    token = _mint_now("followup", event_id=1, correlation_id="hr-1", params={"prompt": "Why do you believe that?"})

    response = await _press(action_client, token, open_id="ou_x")
    assert response.status_code == 200
    assert response.json()["outcome"] == "forwarded followup to probe-action"

    cursor = await store.db.execute(
        "SELECT e.payload_json, d.channel, d.status FROM deliveries d JOIN events e ON e.id = d.event_id"
    )
    rows = [dict(r) for r in await cursor.fetchall()]
    assert [r["channel"] for r in rows] == ["probe-action"]
    envelope = json.loads(rows[0]["payload_json"])
    assert envelope["action"] == {"kind": "followup", "params": {"prompt": "Why do you believe that?"}}
    assert envelope["correlation_id"] == "hr-1" and envelope["actor"] == "ou_x"


async def test_a_press_the_deployment_no_longer_offers_is_refused(action_client):
    """Minted while `approve` was enabled, pressed after it was withdrawn. A
    capability taken away has to stay taken away."""
    token = _mint_now("approve", event_id=1, correlation_id="hr-1")
    response = await _press(action_client, token)
    assert response.status_code == 409, "approve is not in this pipe's card_actions"


async def test_a_refused_token_never_reaches_the_ledger(action_client):
    """A press that fails verification must leave no trace of having been tried:
    a spent-token row for a forged press would let one forgery block the real
    button."""
    store = action_client.app.state.store
    forged = actions.mint("some-other-secret", kind="silence", event_id=1, correlation_id="hr-1", now=time.time())
    assert (await _press(action_client, forged)).status_code == 401
    assert (await action_client.post("/card-action", content=b"{}")).status_code == 400
    assert (await action_client.post("/card-action", content=b"not json")).status_code == 400
    assert await store.recent_actions() == []
    assert await store.active_silence("judge-notify", time.time()) is None


# ── the channels that cannot call back ───────────────────────────────────────


def test_dingtalk_and_wecom_carry_actions_as_links() -> None:
    """Feishu gets buttons because it posts a callback. A DingTalk or WeCom
    webhook robot cannot — its ActionCard buttons are URL jumps — so a real
    button there would do nothing. Without a link these two channels could never
    take part in the feedback the rest of the family now depends on:
    `mattered_pct` would stay null forever and the escalation sweep would read
    every alert as untouched."""
    from hookrelay.processed import Processed

    minted = actions.offered(
        "card-s3cret",
        [{"kind": "silence", "text": "Silence 1h", "minutes": 60}],
        {"silence": {"params": {}}},
        event_id=4,
        correlation_id="hr-4",
        now=time.time(),
    )
    processed = Processed({**PROCESSED, "actions": minted})

    rendered = processed.markdown(heading=True, action_base="https://relay.example")
    assert "[Silence 1h](https://relay.example/card-action?t=" in rendered

    # No base configured — a link nobody can reach is worse than no link.
    assert "card-action" not in processed.markdown(heading=True)


async def test_the_link_only_asks_and_the_post_acts(action_client):
    """A chat client fetches a link to build a preview. A GET that silenced an
    alert would fire when the card was RENDERED rather than when a person
    decided — an alert quietly muted by nobody. So the GET performs nothing and
    the confirming POST does the work."""
    store = action_client.app.state.store
    token = _mint_now("silence", event_id=1, correlation_id="hr-1", params={"minutes": 30})

    preview = await action_client.get(f"/card-action?t={token}")
    assert preview.status_code == 200
    assert "Confirm" in preview.text
    assert await store.recent_actions() == [], "a preview fetch must change nothing"
    assert await store.active_silence("judge-notify", time.time()) is None

    # The form's POST carries the token in the query string — a form has no JSON
    # body to put it in.
    confirmed = await action_client.post(f"/card-action?t={token}")
    assert confirmed.status_code == 200 and confirmed.json()["outcome"].startswith("silenced")
    assert await store.active_silence("judge-notify", time.time()) is not None

    # Still single use: the link works once, as the page says.
    assert (await action_client.post(f"/card-action?t={token}")).json()["outcome"] == "already_done"


async def test_a_link_with_no_token_explains_itself(action_client):
    response = await action_client.get("/card-action")
    assert response.status_code == 200 and "missing its action" in response.text
