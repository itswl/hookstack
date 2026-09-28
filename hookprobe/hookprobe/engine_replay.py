"""replay: a recorded investigation, played back through the real gate.

Not a runtime. The three adapters beside this one drive a model; this one
drives nothing. It reads a script shipped with the package — the tool calls an
investigation made and the report it wrote — plays the calls through the same
`gate.deny_reason` and the same flight recorder a live turn reaches, and
returns the report priced at nothing. What it is for is the first ten minutes:
the quickstart's investigator is up with no key, and a person sees a report, a
card with an approve button, an execution record and an audit page before
deciding whether to hand this node a model.

What it is NOT for, and the word "rehearsal" in every report is there to keep it
honest: it produces no evidence about the system it is deployed beside. Every
number in its reports was written down in advance. A deployment reading a
rehearsal's report as a finding is the failure that word exists to prevent, and
a rehearsal node never holds a credential worth investigating with.

Why it goes through the gate at all, when nothing could be harmed: the loop's
claim is containment by proof. Each script carries exactly one call the
read-only guard refuses — an `rm -rf`, a `kubectl rollout undo` — and the
refusal is real: judged by the one gate, written to the real audit with
`denied: true`, counted as a guard trip on the result. The demo shows the
boundary doing its job rather than describing it.

The Runtime Contract (service.Engine) has five obligations. How a thing that
runs no tool meets them:

  * a gate BEFORE a tool — every recorded call is judged before it is
    announced as done, by the one gate; a refused call is emitted marked and
    never "runs";
  * an audit record the agent cannot edit — there is no agent; this module
    writes the recorder itself, one line per replayed call, in the spawned
    gate's shape plus `replayed: true` so a reader can never mistake the line
    for a live one;
  * a session id that outlives the process — derived from the session key, so
    a resume after a restart lands on the same id and the same script;
  * incremental events — `session` first, then `tool_use`/`tool_done` pairs,
    then `text`, paced so the board's live feed reads like a run and a demo is
    over in seconds;
  * cost or None — `0.0` beside a usage of zero tokens. The contract lets a
    zero mean free only when nothing was spent, and here nothing was.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from hookprobe import audit, gate
from hookprobe.engine import EngineResult
from hookprobe.gate import tool_detail
from hookprobe.redact import redact
from hookprobe.settings import Settings

logger = logging.getLogger("hookprobe.engine.replay")

# The recorded investigations, shipped beside this module: pyproject lists
# `replays/*` as package data, the way it lists the pi gate extension, and the
# allowlist the quickstart arms the rehearsal's procedure with lives there too.
SCRIPTS_DIR = Path(__file__).resolve().parent / "replays"
REHEARSAL_ALLOWLIST = SCRIPTS_DIR / "rehearsal-allowlist"
# The event door writes the alert as `Title: …` into the prompt (events.py). A
# script is chosen on that line and never on the whole prompt, so text in an
# alert's body cannot pick the script.
_TITLE = re.compile(r"^Title:\s*(.*)$", re.MULTILINE)
# Between steps. Long enough that the board's live feed shows a run happening,
# short enough that a script is over before a person has finished reading the
# card that announced it.
PACE_SECONDS = 0.35
_FREE_USAGE = {"input_tokens": 0, "output_tokens": 0}
_REQUIRED = ("name", "match", "steps", "report", "follow_up")


def _joined(value: Any) -> str:
    return "\n".join(str(line) for line in value) if isinstance(value, list) else str(value or "")


def load_scripts(directory: Path = SCRIPTS_DIR) -> list[dict[str, Any]]:
    """Every script in the directory, sorted by file name.

    Refuses a malformed one loudly, at boot: a rehearsal that half-loads is a
    demo that half-runs, discovered by the person it was meant to convince.
    """
    scripts: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        missing = [key for key in _REQUIRED if key not in raw]
        if missing:
            raise ValueError(f"replay script {path.name} lacks {', '.join(missing)}")
        raw["match"] = [str(word).lower() for word in raw["match"]]
        raw["report"] = _joined(raw["report"])
        raw["follow_up"] = _joined(raw["follow_up"])
        scripts.append(raw)
    if not scripts:
        raise ValueError(f"no replay scripts under {directory}")
    if sum(1 for script in scripts if script.get("default")) != 1:
        raise ValueError("exactly one replay script must be marked default")
    return scripts


def choose(scripts: list[dict[str, Any]], message: str) -> dict[str, Any]:
    """The script whose keywords appear in the alert's title, else the default."""
    found = _TITLE.search(message or "")
    title = (found.group(1) if found else "").lower()
    for script in scripts:
        if not script.get("default") and any(word in title for word in script["match"]):
            return script
    return next(script for script in scripts if script.get("default"))


def session_id_for(session_key: str) -> str:
    """Derived, never random: a resume after a restart must land on the same id."""
    return "replay-" + hashlib.sha256(session_key.encode("utf-8")).hexdigest()[:16]


class ReplayEngine:
    """The rehearsal, driven to satisfy the Runtime Contract."""

    def __init__(self, settings: Settings, *, scripts_dir: Path | None = None, pace: float = PACE_SECONDS) -> None:
        self._settings = settings
        self._workdir = settings.workdir
        self._scripts = load_scripts(scripts_dir or SCRIPTS_DIR)
        self._pace = pace
        self._stop = asyncio.Event()
        self._in_flight = 0

    @property
    def scripts(self) -> list[dict[str, Any]]:
        return list(self._scripts)

    def describe_inputs(self, *, resume: str | None = None) -> dict[str, Any]:
        """What this turn puts in front of nothing: the scripts on the shelf, and
        the posture the recorded calls are judged against."""
        return {
            "model": self._settings.model,
            "runtime": "replay",
            "rehearsal": True,
            "scripts": [script["name"] for script in self._scripts],
            "posture": {"bash_guard": self._settings.bash_guard, "mcp_tools": sorted(self._settings.mcp_tools)},
            "resumed": bool(resume),
        }

    async def stop(self) -> bool:
        """Wind the replay down; False when nothing is playing."""
        if self._in_flight <= 0:
            return False
        self._stop.set()
        return True

    def _judge(self, tool: str, tool_input: dict[str, Any]) -> tuple[str, str, str] | None:
        return gate.deny_reason(
            tool,
            tool_input,
            bash_mode=self._settings.bash_guard,
            mcp_allowed=frozenset(self._settings.mcp_tools),
            workdir=self._workdir,
            home=self._workdir / "home",
        )

    async def run(
        self,
        *,
        message: str,
        session_key: str,
        resume: str | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> EngineResult:
        emit = on_event or (lambda event: None)
        started = time.monotonic()
        script = choose(self._scripts, message)
        session_id = resume or session_id_for(session_key)
        # Obligation four: the id is the first thing out, before any step.
        emit({"type": "session", "id": session_id})
        audit_dir = self._workdir / "audit"
        self._in_flight += 1
        self._stop.clear()
        tool_calls = refused = 0
        try:
            if resume:
                # A follow-up on a rehearsal. The recorded investigation has no
                # more evidence to fetch, and the script says so in its own words
                # rather than inventing some.
                await asyncio.sleep(self._pace)
                text = script["follow_up"]
                emit({"type": "text", "text": text[:500]})
                return self._result(text, session_id, messages=1, started=started, refused=0)
            for index, step in enumerate(script["steps"], start=1):
                if self._stop.is_set():
                    text = f"The rehearsal was stopped after {tool_calls} of {len(script['steps'])} recorded calls."
                    emit({"type": "text", "text": text})
                    return self._result(
                        text, session_id, messages=1 + tool_calls, started=started, refused=refused, error="stopped"
                    )
                await asyncio.sleep(self._pace)
                tool = str(step.get("tool") or "Bash")
                tool_input = step.get("input") if isinstance(step.get("input"), dict) else {}
                call_id = f"{session_id}-{index}"
                tool_calls += 1
                emit({"type": "tool_use", "id": call_id, "name": tool, "detail": tool_detail(tool_input)})
                verdict = self._judge(tool, tool_input)
                stamp = round(time.time(), 3)
                if verdict is not None:
                    which, reason, detail = verdict
                    refused += 1
                    audit.append(
                        audit_dir,
                        {
                            "ts": stamp,
                            "session": session_key,
                            "tool": tool,
                            "detail": redact(detail),
                            "denied": True,
                            "guard": which,
                            "reason": reason,
                            "replayed": True,
                        },
                    )
                    emit({"type": "tool_done", "id": call_id, "error": True})
                    continue
                audit.append(
                    audit_dir,
                    {
                        "ts": stamp,
                        "session": session_key,
                        "tool": tool,
                        "detail": tool_detail(tool_input),
                        "error": False,
                        "replayed": True,
                    },
                )
                emit({"type": "tool_done", "id": call_id, "ms": int(step.get("ms") or 0)})
            text = script["report"]
            emit({"type": "text", "text": text[:500]})
            logger.info(
                "rehearsal replayed session=%s script=%s calls=%s refused=%s",
                session_key,
                script["name"],
                tool_calls,
                refused,
            )
            return self._result(text, session_id, messages=1 + tool_calls, started=started, refused=refused)
        finally:
            self._in_flight -= 1

    @staticmethod
    def _result(
        text: str, session_id: str, *, messages: int, started: float, refused: int, error: str | None = None
    ) -> EngineResult:
        return EngineResult(
            text=text,
            message_count=messages,
            # Free, and allowed to say so: nothing was spent behind this zero.
            cost_usd=0.0,
            error=error,
            session_id=session_id,
            usage=dict(_FREE_USAGE),
            duration_ms=int((time.monotonic() - started) * 1000),
            guard_trips=refused,
        )
