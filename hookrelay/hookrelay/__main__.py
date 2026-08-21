"""`python -m hookrelay` — run the relay with uvicorn."""

from __future__ import annotations

import logging
import os

import uvicorn

from hookrelay.app import create_app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    uvicorn.run(
        create_app(),
        # Address the server binds to.
        host=os.environ.get("HOOKRELAY_HOST", "127.0.0.1"),
        # Port the server listens on.
        port=int(os.environ.get("HOOKRELAY_PORT", "8100")),
        log_level="info",
    )


if __name__ == "__main__":
    main()
