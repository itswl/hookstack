"""A replayed signed request is folded, not walked through the pipeline again.

The timestamp on a signed door bounds how long a capture stays useful; on its
own it does not close the window, because the same signature inside it is
accepted again. `replay_token` + `Store.claim_signature` close it at the door,
BEFORE the fuse and the pipeline, so the second copy cannot spend a second
verdict. These tests pin both halves: the door's observable answer, and the
`None` cases where there is nothing a guard can track without guessing.
"""

from __future__ import annotations

import hashlib
import hmac
import json

from hookrelay.security import replay_token

PAYLOAD = {"title": "db down", "message": "x", "state": "alerting"}


def _sig(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _ts_sig(secret: str, body: bytes, ts: int) -> str:
    return hmac.new(secret.encode(), str(ts).encode() + b"." + body, hashlib.sha256).hexdigest()


# ── the door ──────────────────────────────────────────────────────────────


async def test_same_signature_twice_is_folded_the_second_time(client):
    body = json.dumps(PAYLOAD).encode()
    headers = {"X-Hook-Signature": _sig("s3cret", body), "content-type": "application/json"}
    first = await client.post("/hook/grafana", content=body, headers=headers)
    assert first.status_code == 200
    assert first.json()["outcome"] == "routed"

    second = await client.post("/hook/grafana", content=body, headers=headers)
    assert second.status_code == 200
    assert second.json() == {"outcome": "skipped", "skip_code": "replayed_signature"}


async def test_a_replay_is_folded_even_outside_the_content_window(client):
    """The point of a SECOND identity: the content fingerprint only folds inside
    its own window, and the skew outlives it. Changing the source's content
    fingerprint cannot make a replayed capture look new here."""
    body = json.dumps(PAYLOAD).encode()
    headers = {"X-Hook-Signature": _sig("s3cret", body)}
    assert (await client.post("/hook/grafana", content=body, headers=headers)).json()["outcome"] == "routed"
    replay = await client.post("/hook/grafana", content=body, headers=headers)
    assert replay.json()["skip_code"] == "replayed_signature"


async def test_a_timestamped_capture_is_folded_too(client):
    body = json.dumps(PAYLOAD).encode()
    ts = int(__import__("time").time())
    headers = {"X-Hook-Signature": _ts_sig("s3cret", body, ts), "X-Hook-Timestamp": str(ts)}
    assert (await client.post("/hook/grafana", content=body, headers=headers)).json()["outcome"] == "routed"
    replay = await client.post("/hook/grafana", content=body, headers=headers)
    assert replay.json()["skip_code"] == "replayed_signature"


async def test_a_different_signature_is_not_folded(client):
    """A second alert with its own signature must reach the pipeline as before:
    this stage folds signed BYTES, not the content dedup's business."""
    body = json.dumps(PAYLOAD).encode()
    other = json.dumps({**PAYLOAD, "title": "cache miss", "message": "y"}).encode()
    first = await client.post("/hook/grafana", content=body, headers={"X-Hook-Signature": _sig("s3cret", body)})
    second = await client.post("/hook/grafana", content=other, headers={"X-Hook-Signature": _sig("s3cret", other)})
    assert first.json()["outcome"] == "routed"
    assert second.json()["outcome"] == "routed"


async def test_the_cache_is_off_when_the_window_is_zero(settings, cfg):
    """0 is a refusal to cache: the door keeps its bounded-window behaviour and
    neither copy is folded by THIS stage. The knob has to actually do something."""
    import dataclasses

    import httpx

    from hookrelay.app import create_app

    body = json.dumps(PAYLOAD).encode()
    headers = {"X-Hook-Signature": _sig("s3cret", body)}
    off = dataclasses.replace(settings, signature_replay_seconds=0)
    app = create_app(settings=off, cfg=cfg)
    async with (
        httpx.ASGITransport(app=app) as transport,
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://t") as c,
    ):
        first = await c.post("/hook/grafana", content=body, headers=headers)
        second = await c.post("/hook/grafana", content=body, headers=headers)
    assert first.json()["outcome"] == "routed"
    # Unfolded by the replay cache, so the second copy is the content dedup's.
    assert second.json()["skip_code"] != "replayed_signature"


# ── the token ─────────────────────────────────────────────────────────────


def test_replay_token_is_normalised_like_verify_signature():
    assert replay_token("grafana", "SHA256=ABCdef") == "grafana:abcdef"
    assert replay_token("grafana", "abc") == replay_token("grafana", "sha256=abc")


def test_replay_token_is_source_scoped():
    """Two sources sharing a secret must not spend each other's rows."""
    assert replay_token("a", "sig") != replay_token("b", "sig")


def test_replay_token_is_none_when_there_is_nothing_to_track():
    assert replay_token("grafana", None) is None
    assert replay_token("grafana", "") is None
    assert replay_token("", "sig") is None


async def test_claim_signature_is_true_once_then_false(store):
    now = 1_000.0
    assert await store.claim_signature("grafana:abc", 300, now) is True
    assert await store.claim_signature("grafana:abc", 300, now + 1) is False


async def test_claim_signature_forgets_outside_the_window(store):
    """The prune is what keeps the table the size of the window, not of uptime —
    and what lets a genuinely new request with recycled bytes through later."""
    assert await store.claim_signature("grafana:abc", 300, 1_000.0) is True
    assert await store.claim_signature("grafana:abc", 300, 1_301.0) is True
