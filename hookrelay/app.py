"""HookRelay — receive webhooks, decide, fan out. The FastAPI wiring.

Endpoints:
    POST /hook/{source}   inbound door (per-source HMAC)
    GET  /status          queue health + recent decisions (read token)
    POST /silences        quiet a source or everything (admin token)
    DELETE /silences/{id}
    GET  /healthz
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from hookrelay import registry
from hookrelay.config import Config, ConfigError
from hookrelay.delivery import process_due
from hookrelay.pipeline import handle_hook
from hookrelay.security import token_ok
from hookrelay.settings import Settings
from hookrelay.store import Store, now_ts


def create_app(settings: Settings | None = None, cfg: Config | None = None) -> FastAPI:
    """App factory: tests hand in Settings/Config directly; production loads
    them from the environment and config.yaml."""
    app_settings = settings or Settings.load()
    # Plugins load BEFORE config validation: config references adapters,
    # processors and channel types by name, and unknown names must fail the
    # boot, not the first event.
    loaded_plugins = registry.load_plugins(app_settings.plugins_dir)
    if loaded_plugins:
        print(f"[hookrelay] plugins loaded: {', '.join(loaded_plugins)}")
    app_config = cfg or Config.from_file(app_settings.config_path)
    store = Store(app_settings.db_path)

    async def _worker_loop(client: httpx.AsyncClient) -> None:
        while True:
            try:
                await process_due(store, app.state.config, app_settings, client, now_ts())
            except Exception as error:  # noqa: BLE001 — the loop must survive anything
                print(f"[hookrelay] worker error: {error.__class__.__name__}: {error}")
            await asyncio.sleep(app_settings.worker_interval_seconds)

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await store.open()
        client = httpx.AsyncClient(timeout=10.0)
        app.state.http_client = client
        worker = asyncio.create_task(_worker_loop(client))
        try:
            yield
        finally:
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker
            await client.aclose()
            await store.close()

    app = FastAPI(title="hookrelay", lifespan=lifespan)
    app.state.settings = app_settings
    app.state.config = app_config
    app.state.store = store

    # ── the page ──────────────────────────────────────────────────────────
    # One self-contained file, no build step, no CDN. The page itself is a
    # static shell; the DATA sits behind /status and its read token — so
    # serving the shell unauthenticated leaks nothing.
    status_page = (Path(__file__).parent / "status.html").read_text(encoding="utf-8")

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return status_page

    # ── inbound ───────────────────────────────────────────────────────────

    @app.post("/hook/{source_name}")
    async def hook(source_name: str, request: Request) -> JSONResponse:
        source = app.state.config.sources.get(source_name)
        if source is None:
            raise HTTPException(status_code=404, detail="unknown source")
        body = await request.body()
        if len(body) > app_settings.max_body_bytes:
            raise HTTPException(status_code=413, detail="body too large")
        # The source's ADAPTER owns both verification and payload reading —
        # GitHub's header scheme and Grafana's are one plugin apart.
        adapter = registry.SOURCE_ADAPTERS[source.adapter]
        headers = {key.lower(): value for key, value in request.headers.items()}
        if not adapter.verify(source, body, headers):
            raise HTTPException(status_code=401, detail="bad signature")
        try:
            payload = json.loads(body or b"{}")
        except ValueError:
            raise HTTPException(status_code=400, detail="body is not JSON") from None
        extracted = adapter.parse(source, payload)
        result = await handle_hook(
            app.state.store,
            app.state.config,
            source,
            payload,
            now_ts(),
            settings=app_settings,
            client=app.state.http_client,
            extracted=extracted,
        )
        return JSONResponse(result)

    # ── read side ─────────────────────────────────────────────────────────

    def _read_guard(token: str | None) -> None:
        configured = app.state.settings.read_token
        if configured and not token_ok(configured, token):
            raise HTTPException(status_code=401, detail="read token required")

    @app.get("/status")
    async def status(x_read_token: str | None = Header(default=None)) -> dict[str, Any]:
        _read_guard(x_read_token)
        now = now_ts()
        return {
            "queue": await app.state.store.queue_counts(),
            "silences": await app.state.store.list_silences(now),
            "recent": await app.state.store.recent_events(50),
        }

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    # ── admin: silences ───────────────────────────────────────────────────

    def _admin_guard(token: str | None) -> None:
        # No admin token configured = the endpoints do not exist, effectively:
        # an unconfigured instance cannot be muted by whoever finds the port.
        if not token_ok(app.state.settings.admin_token, token):
            raise HTTPException(status_code=403, detail="admin token required")

    @app.post("/silences")
    async def create_silence(request: Request, x_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
        _admin_guard(x_admin_token)
        data = await request.json()
        source = str(data.get("source", "*"))
        if source != "*" and source not in app.state.config.sources:
            raise HTTPException(status_code=400, detail="unknown source")
        minutes = int(data.get("minutes", 60))
        if minutes < 1 or minutes > 7 * 24 * 60:
            raise HTTPException(status_code=400, detail="minutes out of range (1..10080)")
        now = now_ts()
        silence_id = await app.state.store.add_silence(source, now + minutes * 60, str(data.get("note", "")), now)
        return {"id": silence_id, "source": source, "until_ts": now + minutes * 60}

    # ── admin: config (the FILE stays the source of truth) ───────────────
    # The page is an editor for config.yaml, not a second config store:
    # GET returns the raw text (${ENV} refs, never resolved secrets), PUT
    # validates + writes atomically + hot-swaps, reload re-reads the file.
    # Validation failure changes NOTHING — the running config keeps serving.

    def _config_summary(loaded: Config) -> dict[str, Any]:
        return {
            "sources": sorted(loaded.sources),
            "channels": sorted(loaded.channels),
            "routes": [route.name for route in loaded.routes],
            "pipeline": [stage.name for stage in loaded.pipeline],
        }

    @app.get("/config")
    async def get_config(x_admin_token: str | None = Header(default=None)) -> JSONResponse:
        _admin_guard(x_admin_token)
        path = Path(app.state.settings.config_path)
        if not path.is_file():
            raise HTTPException(status_code=404, detail=f"no config file at {path}")
        return JSONResponse({"path": str(path), "yaml": path.read_text(encoding="utf-8")})

    @app.put("/config")
    async def put_config(request: Request, x_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
        _admin_guard(x_admin_token)
        text = (await request.body()).decode("utf-8")
        try:
            import yaml as _yaml

            candidate = Config.from_dict(_yaml.safe_load(text) or {})
        except (ConfigError, Exception) as error:  # noqa: BLE001 — every parse error is a 400
            raise HTTPException(status_code=400, detail=f"{error.__class__.__name__}: {error}") from None
        path = Path(app.state.settings.config_path)
        # Atomic replace: never leave a half-written config on disk.
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent) or ".", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_name, path)
        app.state.config = candidate
        return {"applied": True, **_config_summary(candidate)}

    @app.post("/config/reload")
    async def reload_config(x_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
        _admin_guard(x_admin_token)
        try:
            candidate = Config.from_file(app.state.settings.config_path)
        except (ConfigError, FileNotFoundError, Exception) as error:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"{error.__class__.__name__}: {error}") from None
        app.state.config = candidate
        return {"applied": True, **_config_summary(candidate)}

    # ── admin: dead-letter retry ──────────────────────────────────────────

    @app.post("/deliveries/{delivery_id}/retry")
    async def retry_delivery(delivery_id: int, x_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
        _admin_guard(x_admin_token)
        if not await app.state.store.retry_delivery(delivery_id, now_ts()):
            raise HTTPException(status_code=404, detail="no dead delivery with that id")
        return {"requeued": delivery_id}

    @app.delete("/silences/{silence_id}")
    async def remove_silence(silence_id: int, x_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
        _admin_guard(x_admin_token)
        if not await app.state.store.delete_silence(silence_id):
            raise HTTPException(status_code=404, detail="no such silence")
        return {"deleted": silence_id}

    return app
