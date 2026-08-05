"""The HTTP surface: signatures at the door, tokens on the read/admin sides."""

from __future__ import annotations

import hashlib
import hmac
import json

import httpx
import pytest

from hookrelay.app import create_app

PAYLOAD = {"title": "db down", "message": "x", "state": "alerting"}


def _sig(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture
async def client(settings, cfg):
    app = create_app(settings=settings, cfg=cfg)
    # lifespan by hand: ASGITransport does not run it.
    async with (
        httpx.ASGITransport(app=app) as transport,
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://t") as c,
    ):
        yield c


async def test_signed_hook_round_trip(client):
    body = json.dumps(PAYLOAD).encode()
    response = await client.post(
        "/hook/grafana",
        content=body,
        headers={"X-Hook-Signature": _sig("s3cret", body), "content-type": "application/json"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["outcome"] == "routed"
    assert data["channels"] == ["feishu-main", "ding-main", "mirror"]


async def test_bad_signature_is_401_and_leaves_no_trace(client):
    body = json.dumps(PAYLOAD).encode()
    response = await client.post("/hook/grafana", content=body, headers={"X-Hook-Signature": "sha256=wrong"})
    assert response.status_code == 401
    status = await client.get("/status", headers={"X-Read-Token": "read-t"})
    assert status.json()["recent"] == []


async def test_unsigned_source_accepts_without_header(client):
    response = await client.post("/hook/ci", json={"job": "build", "detail": "ok"})
    assert response.status_code == 200


async def test_unknown_source_is_404(client):
    assert (await client.post("/hook/nope", json={})).status_code == 404


async def test_status_requires_read_token_when_configured(client):
    assert (await client.get("/status")).status_code == 401
    response = await client.get("/status", headers={"X-Read-Token": "read-t"})
    assert response.status_code == 200
    assert set(response.json().keys()) == {"queue", "silences", "recent"}


async def test_silence_lifecycle_via_admin_api(client):
    created = await client.post(
        "/silences", json={"source": "grafana", "minutes": 30, "note": "mw"}, headers={"X-Admin-Token": "admin-t"}
    )
    assert created.status_code == 200
    silence_id = created.json()["id"]

    body = json.dumps(PAYLOAD).encode()
    hooked = await client.post("/hook/grafana", content=body, headers={"X-Hook-Signature": _sig("s3cret", body)})
    assert hooked.json()["skip_code"] == "silenced"

    deleted = await client.delete(f"/silences/{silence_id}", headers={"X-Admin-Token": "admin-t"})
    assert deleted.status_code == 200

    # Same fingerprint would dedup against the silenced event — vary the title
    # to prove routing is live again rather than testing the dedup gate twice.
    body2 = json.dumps(dict(PAYLOAD, title="db down again")).encode()
    hooked2 = await client.post("/hook/grafana", content=body2, headers={"X-Hook-Signature": _sig("s3cret", body2)})
    assert hooked2.json()["outcome"] == "routed"


async def test_admin_endpoints_refuse_without_token(client):
    assert (await client.post("/silences", json={"minutes": 5})).status_code == 403
    assert (await client.delete("/silences/1")).status_code == 403
