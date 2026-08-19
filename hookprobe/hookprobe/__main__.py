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


def _check_transcripts_writable() -> None:
    """Say so at boot when the engine cannot keep transcripts.

    A bind mount under $HOME/.claude makes Docker create the parents as root,
    which leaves the non-root app unable to write there. First turns still
    work, so the damage stays invisible until someone sends a follow-up and
    gets "No conversation found" — worth one loud line at startup instead.
    """
    transcripts = Path.home() / ".claude"
    try:
        transcripts.mkdir(parents=True, exist_ok=True)
        probe = transcripts / ".write-check"
        probe.touch()
        probe.unlink()
    except OSError as exc:
        logging.getLogger("hookprobe").error(
            "%s is not writable (%s) — the engine keeps session transcripts there, so follow-up "
            "turns will fail with 'No conversation found'. On a fresh volume this usually means a "
            "bind mount created the parent as root: chown it to uid 10001.",
            transcripts,
            exc,
        )


def _warn_open_doors(settings: Settings) -> None:
    """Say at boot which doors are open, where an operator will actually look.

    Both of these are deliberate configurations for a private network, and
    neither is a state to drift into. The event door is the loud one: it is the
    only mutating route with no bearer token — that is by design, it is the
    pipe's door — and verify_timestamped returns True on an empty secret, which
    is the default. So an operator who sets HOOKPROBE_TOKEN and stops there has
    locked every door a person uses and left open the one that spends money
    without anyone asking.
    """
    logger = logging.getLogger("hookprobe")
    if not settings.token:
        logger.warning("HOOKPROBE_TOKEN is empty — accepting unauthenticated requests")
    if not settings.event_secret:
        logger.warning(
            "HOOKPROBE_EVENT_SECRET is empty — POST /hooks/event accepts unsigned events from "
            "anyone who can reach this port, and that door starts paid investigations. The bearer "
            "token does not cover it: set the secret to the value hookrelay signs its to-probe "
            "channel with."
        )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings.load()
    settings.workdir.mkdir(parents=True, exist_ok=True)
    # $HOME holds the engine's session transcripts (~/.claude); the Dockerfile
    # points it at the persistent volume so follow-up turns survive restarts.
    Path.home().mkdir(parents=True, exist_ok=True)
    _check_transcripts_writable()
    store = RunStore(settings.workdir / "results")
    engine = ClaudeAgentEngine(settings)
    service = RunService(settings, engine, store)
    _warn_open_doors(settings)
    uvicorn.run(create_app(settings, service), host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
