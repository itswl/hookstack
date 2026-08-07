"""`python -m hookjudge`."""

import os

import uvicorn

from hookjudge.app import create_app


def main() -> None:
    uvicorn.run(
        create_app(),
        host=os.environ.get("HOOKJUDGE_HOST", "127.0.0.1"),
        port=int(os.environ.get("HOOKJUDGE_PORT", "8200")),
        log_level="info",
    )


if __name__ == "__main__":
    main()
