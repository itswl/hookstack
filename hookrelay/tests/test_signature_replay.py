"""A replayed signed request is folded, not walked through the pipeline again.

The timestamp on a signed door bounds how long a capture stays useful; on its
own it does not close the window, because the same signature inside it is
accepted again. `replay_token` + the store's signature lease
(`acquire_signature` / `complete_signature`) close it at the door. These tests pin three things: the door's observable
answer, the `None` cases where there is nothing a guard can track without
guessing, and — the one a first cut got wrong — that the signature is claimed
only once the pipeline has ANSWERED, so a retry after a 5xx or a 429 is
processed rather than folded as a replay.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import hashlib
import hmac
import json

import httpx

import hookrelay.app as app_module
from hookrelay.app import create_app
from hookrelay.security import replay_token, verify_signature
from hookrelay.store import Store

PAYLOAD = {"title": "db down", "message": "x", "state": "alerting"}


def _sig(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _ts_sig(secret: str, body: bytes, ts: int) -> str:
    return hmac.new(secret.encode(), str(ts).encode() + b"." + body, hashlib.sha256).hexdigest()


@contextlib.asynccontextmanager
async def _serve(app, *, raise_app_exceptions: bool = True):
    """An app served in process, so a test can reach `app.state` and monkeypatch.

    `raise_app_exceptions=False` is what turns a fault in the pipeline into a 500
    RESPONSE, which is the case a sender retries against.
    """
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=raise_app_exceptions)
    async with (
        transport,
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://t") as client,
    ):
        yield client


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
    body = json.dumps(PAYLOAD).encode()
    headers = {"X-Hook-Signature": _sig("s3cret", body)}
    app = create_app(settings=dataclasses.replace(settings, signature_replay_seconds=0), cfg=cfg)
    async with _serve(app) as c:
        first = await c.post("/hook/grafana", content=body, headers=headers)
        second = await c.post("/hook/grafana", content=body, headers=headers)
    assert first.json()["outcome"] == "routed"
    # Unfolded by the replay cache, so the second copy is the content dedup's.
    assert second.json()["skip_code"] != "replayed_signature"


async def test_a_retry_after_a_5xx_is_processed(settings, cfg, monkeypatch):
    """The claim is written only once the pipeline has ANSWERED. Claimed up front,
    a fault here would make the sender's identical retry answer as a replay and
    the event would be lost while both sides believed it had been handled."""
    real = app_module.handle_hook
    calls = {"n": 0}

    async def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("the pipeline fell over")
        return await real(*args, **kwargs)

    monkeypatch.setattr(app_module, "handle_hook", flaky)
    body = json.dumps(PAYLOAD).encode()
    headers = {"X-Hook-Signature": _sig("s3cret", body)}
    app = create_app(settings=settings, cfg=cfg)
    async with _serve(app, raise_app_exceptions=False) as c:
        first = await c.post("/hook/grafana", content=body, headers=headers)
        assert first.status_code == 500
        # The same bytes again: the retry must be processed, not folded.
        second = await c.post("/hook/grafana", content=body, headers=headers)
    assert second.status_code == 200
    assert second.json()["outcome"] == "routed"


async def test_a_retry_after_the_fuse_429_is_processed(settings, cfg):
    """The hard fuse stores nothing, so nothing about it may burn the signature:
    once the storm passes, the retry is processed rather than folded."""
    body = json.dumps(PAYLOAD).encode()
    headers = {"X-Hook-Signature": _sig("s3cret", body)}
    app = create_app(settings=settings, cfg=cfg)
    verdicts = iter(["reject", "pass"])
    app.state.fuse.check = lambda *args, **kwargs: next(verdicts)
    async with _serve(app) as c:
        first = await c.post("/hook/grafana", content=body, headers=headers)
        assert first.status_code == 429
        second = await c.post("/hook/grafana", content=body, headers=headers)
    assert second.status_code == 200
    assert second.json()["outcome"] == "routed"


async def test_a_second_request_after_success_is_still_folded(client):
    """And the claim DOES happen on the success path — the fold must not have been
    traded away for the retry behaviour above."""
    body = json.dumps(PAYLOAD).encode()
    headers = {"X-Hook-Signature": _sig("s3cret", body)}
    assert (await client.post("/hook/grafana", content=body, headers=headers)).json()["outcome"] == "routed"
    again = await client.post("/hook/grafana", content=body, headers=headers)
    assert again.json() == {"outcome": "skipped", "skip_code": "replayed_signature"}


async def test_simultaneous_replay_waits_for_the_first_request(settings, cfg, monkeypatch):
    entered = asyncio.Event()
    release = asyncio.Event()
    real = app_module.handle_hook
    calls = 0

    async def delayed(*args, **kwargs):
        nonlocal calls
        calls += 1
        entered.set()
        await asyncio.wait_for(release.wait(), 2)
        return await real(*args, **kwargs)

    monkeypatch.setattr(app_module, "handle_hook", delayed)
    body = json.dumps(PAYLOAD).encode()
    headers = {"X-Hook-Signature": _sig("s3cret", body)}
    app = create_app(settings=settings, cfg=cfg)
    async with _serve(app) as client:
        first = asyncio.create_task(client.post("/hook/grafana", content=body, headers=headers))
        await asyncio.wait_for(entered.wait(), 2)
        concurrent = await client.post("/hook/grafana", content=body, headers=headers)
        assert concurrent.status_code == 503 and concurrent.headers["retry-after"] == "1"
        release.set()
        assert (await first).json()["outcome"] == "routed"
        retry = await client.post("/hook/grafana", content=body, headers=headers)
        assert retry.json()["skip_code"] == "replayed_signature"
    assert calls == 1


async def test_expired_processing_lease_can_be_recovered(store):
    assert await store.acquire_signature("token", "first", 300, 1000, 120) == "acquired"
    assert await store.acquire_signature("token", "second", 300, 1001, 120) == "busy"
    assert await store.acquire_signature("token", "second", 300, 1121, 120) == "acquired"
    assert not await store.complete_signature("token", "first", 300, 1122)
    assert await store.complete_signature("token", "second", 300, 1122)
    assert await store.acquire_signature("token", "third", 300, 1123, 120) == "completed"


async def test_separate_stores_serialize_signature_acquisition(tmp_path):
    first = Store(str(tmp_path / "shared.db"))
    second = Store(str(tmp_path / "shared.db"))
    await first.open()
    await second.open()
    try:
        outcomes = await asyncio.gather(
            first.acquire_signature("token", "first", 300, 1000, 120),
            second.acquire_signature("token", "second", 300, 1000, 120),
        )
        assert sorted(outcomes) == ["acquired", "busy"]
    finally:
        await first.close()
        await second.close()


async def test_a_failed_renewal_lets_the_request_finish(settings, cfg, monkeypatch):
    """Cancelling on a failed renewal left the sender a dropped connection and
    the pipeline half-run. The lease is still the request's, so it completes,
    and the next identical copy is the replay it is."""
    entered, failed = asyncio.Event(), asyncio.Event()
    real_handle, real_sleep = app_module.handle_hook, asyncio.sleep

    async def fast_heartbeat(delay):
        if delay == 40:
            await entered.wait()
            await real_sleep(0.01)
            return
        await real_sleep(delay)

    async def handle(*args, **kwargs):
        entered.set()
        await failed.wait()
        return await real_handle(*args, **kwargs)

    async def broken_renew(*args, **kwargs):
        failed.set()
        raise OSError("database unavailable")

    monkeypatch.setattr(app_module.asyncio, "sleep", fast_heartbeat)
    monkeypatch.setattr(app_module, "handle_hook", handle)
    body = json.dumps(PAYLOAD).encode()
    headers = {"X-Hook-Signature": _sig("s3cret", body)}
    app = create_app(settings=settings, cfg=cfg)
    async with _serve(app) as client:
        monkeypatch.setattr(app.state.store, "renew_signature", broken_renew)
        first = await asyncio.wait_for(client.post("/hook/grafana", content=body, headers=headers), 5)
        monkeypatch.setattr(app_module.asyncio, "sleep", real_sleep)
        again = await client.post("/hook/grafana", content=body, headers=headers)
    assert first.status_code == 200 and first.json()["outcome"] == "routed"
    assert again.json()["skip_code"] == "replayed_signature"


async def test_a_lease_lost_before_completion_answers_503(settings, cfg, monkeypatch):
    async def lost(*args, **kwargs):
        return False

    body = json.dumps(PAYLOAD).encode()
    headers = {"X-Hook-Signature": _sig("s3cret", body)}
    app = create_app(settings=settings, cfg=cfg)
    async with _serve(app) as client:
        monkeypatch.setattr(app.state.store, "complete_signature", lost)
        response = await client.post("/hook/grafana", content=body, headers=headers)
    assert response.status_code == 503 and "lease lost" in response.json()["detail"]


async def test_a_replay_of_a_handled_request_takes_no_write_lock(settings, cfg, monkeypatch):
    """A bulk replay of one capture used to cost a write per copy."""
    body = json.dumps(PAYLOAD).encode()
    headers = {"X-Hook-Signature": _sig("s3cret", body)}
    app = create_app(settings=settings, cfg=cfg)
    async with _serve(app) as client:
        assert (await client.post("/hook/grafana", content=body, headers=headers)).json()["outcome"] == "routed"
        store, writes = app.state.store, []
        real_write = store._write

        def counting_write():
            writes.append(1)
            return real_write()

        monkeypatch.setattr(store, "_write", counting_write)
        for _ in range(5):
            again = await client.post("/hook/grafana", content=body, headers=headers)
            assert again.json()["skip_code"] == "replayed_signature"
    assert writes == [], "a replay of a handled request wrote to the ledger"


def test_the_lease_length_is_a_setting_with_a_floor(monkeypatch):
    from hookrelay.settings import Settings

    assert Settings.load().signature_lease_seconds == 120
    monkeypatch.setenv("HOOKRELAY_SIGNATURE_LEASE_SECONDS", "45")
    assert Settings.load().signature_lease_seconds == 45
    monkeypatch.setenv("HOOKRELAY_SIGNATURE_LEASE_SECONDS", "3")
    assert Settings.load().signature_lease_seconds == 10


def test_nonfinite_signed_timestamps_are_refused():
    body = b"{}"
    for timestamp in ("NaN", "inf", "-inf"):
        signature = hmac.new(b"s3cret", timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
        assert not verify_signature("s3cret", body, signature, timestamp_value=timestamp, require_timestamp=True)


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


async def test_a_completed_signature_is_spent_once(store):
    now = 1_000.0
    assert await store.acquire_signature("grafana:abc", "a", 300, now, 120) == "acquired"
    assert await store.complete_signature("grafana:abc", "a", 300, now) is True
    assert await store.acquire_signature("grafana:abc", "b", 300, now + 1, 120) == "completed"


async def test_a_held_signature_is_busy_until_its_holder_lets_go(store):
    """Released after a failure, the next identical request is processed —
    the property the spent-after-answer order exists for."""
    assert await store.acquire_signature("grafana:abc", "a", 300, 1_000.0, 120) == "acquired"
    assert await store.acquire_signature("grafana:abc", "b", 300, 1_000.0, 120) == "busy"
    await store.release_signature("grafana:abc", "a")
    assert await store.acquire_signature("grafana:abc", "b", 300, 1_000.0, 120) == "acquired"


async def test_a_spent_signature_is_forgotten_outside_the_window(store):
    """The prune is what keeps the table the size of the window, not of uptime —
    and what lets a genuinely new request with recycled bytes through later."""
    assert await store.acquire_signature("grafana:abc", "a", 300, 1_000.0, 120) == "acquired"
    assert await store.complete_signature("grafana:abc", "a", 300, 1_000.0) is True
    assert await store.acquire_signature("grafana:abc", "b", 300, 1_301.0, 120) == "acquired"
