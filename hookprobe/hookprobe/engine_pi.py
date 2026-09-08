"""pi as a hookprobe runtime: the third implementation, and the one that made
the contract worth writing.

Codex proved the contract could be met twice. pi is where the two runtimes stop
agreeing, and every disagreement below is a place where an adapter written by
reading signatures alone would have satisfied the type and broken the service.

**Its gate is an extension, and extensions are TypeScript.** `pi_gate.ts` ships
beside this module and does exactly one thing: hand the call to
`python -m hookprobe.gate` and translate the answer. The policy is not in it.
pi names its tools in lower case and hookstack's guards speak the names Claude
Code and Codex share, so something has to translate, and translation is all it
does.

**A blocked call still produces tool events.** Codex omits the tool entirely
when the gate refuses; pi emits `tool_execution_start`, preflights, and then
ends the call with the refusal as its result. Both are honest and the feed has
to read the same either way, so a denied call is marked on the step rather than
hidden — which is arguably the better record, since it shows what the agent
tried.

**Cost is reported, and reporting it is the trap.** pi prices a turn from its
model catalog and puts `usage.cost.total` on the message. A model served through
a private gateway is not in that catalog, so the number comes back `0` beside
six thousand real tokens. That is the exact shape the contract forbids — a plain
`0.0` standing in for "nobody counted" — and a budget breaker fed those would
watch an unattended node spend all week and see nothing at all. So a zero with
tokens behind it is reported as `None`, and only a zero with no tokens is
allowed to mean free.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import hookprobe
from hookprobe import gate
from hookprobe.engine import EngineResult, engine_error, file_fact
from hookprobe.gate import tool_detail
from hookprobe.settings import Settings

logger = logging.getLogger("hookprobe.engine.pi")

_PACKAGE_ROOT = str(Path(hookprobe.__file__).resolve().parent.parent)
# The extension that reaches the gate, shipped beside this module.
GATE_EXTENSION = Path(hookprobe.__file__).resolve().parent / "pi_gate.ts"

# What the operator's pi directory has to lend this node. Copied rather than
# pointed at: the home is owned, for the same reason the Codex one is.
_CONFIG_FILES = ("models.json", "auth.json", "settings.json")


class PiEngine:
    """The pi CLI, driven to satisfy the Runtime Contract."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._workdir = settings.workdir
        self._binary = settings.pi_binary
        self._home = settings.workdir / "pi-home"
        self._proc: asyncio.subprocess.Process | None = None
        self._gate_proven = False

    # ------------------------------------------------------------------ setup

    def prepare_home(self) -> Path:
        """Build the pi config directory this adapter owns."""
        self._home.mkdir(parents=True, exist_ok=True)
        (self._home / "sessions").mkdir(exist_ok=True)
        template = self._settings.pi_config
        if template and template.is_dir():
            for name in _CONFIG_FILES:
                source = template / name
                if source.is_file():
                    shutil.copyfile(source, self._home / name)
                    # auth.json is a credential; it arrives with the operator's
                    # permissions and should not leave with looser ones.
                    (self._home / name).chmod(0o600)
        return self._home

    def _env(self, session_key: str) -> dict[str, str]:
        env = gate.environment(self._settings, session_key, package_root=_PACKAGE_ROOT)
        env["PI_CODING_AGENT_DIR"] = str(self._home)
        # Transcripts on the run's own volume, which is what makes a session
        # outlive the restart that resume exists for.
        env["PI_CODING_AGENT_SESSION_DIR"] = str(self._home / "sessions")
        # No update checks, no telemetry call, no version ping: an investigation
        # starting is not the moment to depend on the internet being up.
        env["PI_OFFLINE"] = "1"
        # How the extension reaches the gate. It is the one thing pi_gate.ts
        # reads from the environment, and everything else it reads is the call.
        env["HOOKPROBE_GATE_PYTHON"] = self._settings.pi_python
        return env

    def verify_gate(self) -> None:
        """Prove this node can gate a tool before it runs one, or refuse to run."""
        if self._gate_proven:
            return
        if shutil.which(self._binary) is None and not Path(self._binary).is_file():
            raise RuntimeError(
                f"HOOKPROBE_RUNTIME=pi but {self._binary!r} is not on this node's PATH. "
                "The adapter drives the CLI as a subprocess; there is nothing to drive."
            )
        if not GATE_EXTENSION.is_file():
            raise RuntimeError(
                f"the pi gate extension is missing at {GATE_EXTENSION}. Without it pi runs with no "
                "posture at all, so this node does not start."
            )
        gate.verify(self._settings.pi_python, self._env("probe:gate-selftest"))
        self._gate_proven = True
        logger.info("tool gate verified: %s -m hookprobe.gate refuses what it must", self._settings.pi_python)

    # -------------------------------------------------------------------- run

    def _argv(self, message: str, resume: str | None) -> list[str]:
        argv = [
            self._binary,
            "--mode",
            "json",
            "--print",
            "--provider",
            self._settings.pi_provider,
            "--model",
            self._settings.model,
            "--extension",
            str(GATE_EXTENSION),
            # Project-local settings and extensions are attacker-influenced: the
            # workspace holds files a run wrote. Nothing from it is trusted.
            "--no-approve",
        ]
        if resume:
            argv += ["--session", resume]
        # `--` first, so a prompt that opens with a dash is a prompt and not a
        # flag. The event door caps an alert body at 4000 bytes; a follow-up
        # thread is longer but nowhere near this platform's argv limit.
        argv += ["--", message]
        return argv

    async def run(
        self,
        *,
        message: str,
        session_key: str,
        resume: str | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> EngineResult:
        emit = on_event or (lambda event: None)
        self.prepare_home()
        self.verify_gate()
        started = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            *self._argv(message, resume),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._env(session_key),
            cwd=str(self._workdir),
        )
        self._proc = proc
        if proc.stdout is None:
            raise RuntimeError("pi started without the pipe this adapter reads the turn through")
        state = _Turn(emit)
        try:
            async for raw in proc.stdout:
                line = raw.decode("utf-8", "replace").strip()
                if line:
                    state.feed(line)
            await proc.wait()
        finally:
            self._proc = None
        stderr = (await proc.stderr.read()).decode("utf-8", "replace") if proc.stderr else ""
        return state.result(
            duration_ms=int((time.monotonic() - started) * 1000),
            returncode=proc.returncode,
            stderr=stderr,
        )

    async def stop(self) -> bool:
        proc = self._proc
        if proc is None or proc.returncode is not None:
            return False
        proc.terminate()
        return True

    def describe_inputs(self, *, resume: str | None = None) -> dict[str, Any]:
        return {
            "model": self._settings.model,
            "runtime": "pi",
            "provider": self._settings.pi_provider,
            "home": str(self._home),
            "gate_extension": str(GATE_EXTENSION),
            "memory": file_fact(self._workdir / "AGENTS.md"),
            "posture": {"bash_guard": self._settings.bash_guard, "mcp_tools": sorted(self._settings.mcp_tools)},
            "resumed": bool(resume),
        }


class _Turn:
    """One turn's worth of pi's JSON stream, folded into an EngineResult."""

    def __init__(self, emit: Callable[[dict[str, Any]], None]) -> None:
        self._emit = emit
        self.session_id: str | None = None
        self.text = ""
        self.messages = 0
        self.usage: dict[str, Any] | None = None
        self.errors: list[str] = []
        self.session_was_first = False
        self._seen_any = False

    def feed(self, line: str) -> None:
        try:
            event = json.loads(line)
        except ValueError:
            logger.debug("pi emitted a line that was not JSON: %s", line[:200])
            return
        if not isinstance(event, dict):
            return
        kind = str(event.get("type") or "")

        if kind == "session":
            found = str(event.get("id") or "")
            if found:
                # pi puts the session header first by construction, which is
                # obligation four met by the runtime rather than by the adapter
                # digging for it.
                self.session_id = found
                self.session_was_first = not self._seen_any
                self._emit({"type": "session", "id": found})
            self._seen_any = True
            return

        self._seen_any = True
        if kind == "tool_execution_start":
            self._emit(
                {
                    "type": "tool_use",
                    "id": str(event.get("toolCallId") or ""),
                    "name": str(event.get("toolName") or ""),
                    "detail": tool_detail(event.get("args")),
                }
            )
        elif kind == "tool_execution_end":
            # pi runs the preflight AFTER announcing the call, so a refused tool
            # still has a step. Marked rather than hidden: what the agent tried
            # is part of the record.
            failed = bool(event.get("isError")) or bool((event.get("result") or {}).get("isError"))
            self._emit(
                {"type": "tool_done", "id": str(event.get("toolCallId") or ""), **({"error": True} if failed else {})}
            )
        elif kind == "message_end":
            self._message(event.get("message") or {})
        elif kind == "error":
            self.errors.append(str(event.get("message") or event.get("error") or "pi reported an error"))

    def _message(self, message: dict[str, Any]) -> None:
        if message.get("role") != "assistant":
            return
        self.messages += 1
        usage = message.get("usage")
        if isinstance(usage, dict):
            self.usage = usage
        text = "\n".join(
            str(block.get("text") or "")
            for block in message.get("content") or []
            if isinstance(block, dict) and block.get("type") == "text" and block.get("text")
        )
        if text:
            self.text = text
            self._emit({"type": "text", "text": text[:500]})

    def cost(self) -> float | None:
        """Money, or None — see the module docstring for why this is not `or 0.0`."""
        usage = self.usage or {}
        priced = usage.get("cost")
        if not isinstance(priced, dict):
            return None
        total = priced.get("total")
        if not isinstance(total, int | float):
            return None
        if total == 0 and (usage.get("totalTokens") or 0) > 0:
            # Tokens were spent and pi had no price for this model. "Free" and
            # "unpriced" are different facts and the ledger keeps them apart.
            return None
        return float(total)

    def result(self, *, duration_ms: int, returncode: int | None, stderr: str) -> EngineResult:
        return EngineResult(
            text=self.text,
            message_count=self.messages,
            cost_usd=self.cost(),
            error=self._error(returncode, stderr),
            session_id=self.session_id,
            usage=self.usage,
            duration_ms=duration_ms,
        )

    def _error(self, returncode: int | None, stderr: str) -> str | None:
        if returncode not in (0, None):
            tail = stderr.strip().splitlines()[-1] if stderr.strip() else ""
            return f"pi exited {returncode}{': ' + tail if tail else ''}"
        if not self.text:
            first = self.errors[0] if self.errors else "pi produced no answer"
            return engine_error(None, first) or first
        # A turn that produced an answer did not fail; whatever else was logged
        # belongs on the feed. Third time this service has learned that.
        return None
