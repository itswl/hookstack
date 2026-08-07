"""`python -m hookrelay` — run the relay with uvicorn."""

import os

import uvicorn

from hookrelay.app import create_app


def main() -> None:
    uvicorn.run(
        create_app(),
        host=os.environ.get("HOOKRELAY_HOST", "127.0.0.1"),
        port=int(os.environ.get("HOOKRELAY_PORT", "8100")),
        log_level="info",
    )


if __name__ == "__main__":
    main()
