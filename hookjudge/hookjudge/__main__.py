"""`python -m hookjudge`."""

from __future__ import annotations

import logging

import uvicorn

from hookjudge.app import create_app
from hookjudge.settings import Settings


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings.load()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
