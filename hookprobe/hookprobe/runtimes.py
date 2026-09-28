"""Which runtimes this service can run a turn on, and the one place naming them.

A registry rather than an `if` in `__main__`, for one reason: the conformance
suite reads this dict. A third adapter cannot be added without appearing in the
suite that judges it, which is the only mechanism here that survives the person
who wrote the contract forgetting about it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from hookprobe.settings import Settings


def _claude(settings: Settings) -> Any:
    from hookprobe.engine import ClaudeAgentEngine

    return ClaudeAgentEngine(settings)


def _codex(settings: Settings) -> Any:
    from hookprobe.engine_codex import CodexEngine

    return CodexEngine(settings)


def _pi(settings: Settings) -> Any:
    from hookprobe.engine_pi import PiEngine

    return PiEngine(settings)


def _replay(settings: Settings) -> Any:
    from hookprobe.engine_replay import ReplayEngine

    return ReplayEngine(settings)


# Imported lazily inside the factories: the Claude adapter drags the SDK in with
# it, and a deployment running codex should not need it installed to boot.
#
# Four names, three runtimes. `replay` is the rehearsal (engine_replay.py): a
# recorded investigation played back through the same gate and recorder, with
# no model behind it. It is in this registry rather than special-cased in
# `__main__` for the reason the registry exists — the conformance suite reads
# this dict, so the one adapter that could most easily be waved through as
# "just a demo" is judged by the same suite as the rest.
ADAPTERS: dict[str, Callable[[Settings], Any]] = {"claude": _claude, "codex": _codex, "pi": _pi, "replay": _replay}


def build_engine(settings: Settings) -> Any:
    """The engine this node runs turns on.

    Refuses an unknown name rather than falling back to the default. A typo in
    `HOOKPROBE_RUNTIME` that silently started the Claude adapter would be a node
    reporting one runtime on /v1/agent and running another, which is exactly the
    claim this contract exists to keep honest.
    """
    try:
        return ADAPTERS[settings.runtime](settings)
    except KeyError:
        raise ValueError(
            f"HOOKPROBE_RUNTIME={settings.runtime!r} is not a runtime this build has an adapter for. "
            f"Known: {', '.join(sorted(ADAPTERS))}."
        ) from None
