"""Entrypoint: wire settings -> store -> engine -> app, then serve."""

from __future__ import annotations

import logging
from pathlib import Path

import uvicorn

from hookprobe.app import create_app
from hookprobe.engine import ClaudeAgentEngine
from hookprobe.runs import RunStore
from hookprobe.service import RunService
from hookprobe.settings import Settings


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings.load()
    settings.workdir.mkdir(parents=True, exist_ok=True)
    # $HOME holds the engine's session transcripts (~/.claude); the Dockerfile
    # points it at the persistent volume so follow-up turns survive restarts.
    Path.home().mkdir(parents=True, exist_ok=True)
    store = RunStore(settings.workdir / "results")
    engine = ClaudeAgentEngine(settings)
    service = RunService(settings, engine, store)
    if not settings.token:
        logging.getLogger("hookprobe").warning("HOOKPROBE_TOKEN is empty — accepting unauthenticated requests")
    uvicorn.run(create_app(settings, service), host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
