"""Codex CLI as a hookprobe runtime: the second adapter the contract needed.

`service.Engine` had one implementation for its whole life, and a contract
satisfied once is a description of that implementation. This is the second one,
and the point of it is not portability for its own sake — it is that the
containment story has to survive a runtime swap. Everything this node claims
about itself (`readonly` means the shell cannot mutate, every tool call is on
the flight recorder, a crash does not throw the investigation away) is claimed
about the runtime, not about the Claude SDK.

Four decisions worth keeping, because each was a fork in the road:

**A subprocess reading JSONL, not the app-server.** `codex exec --json` already
emits everything the contract asks for, `thread.started` first. The app-server
is a JSON-RPC daemon with a lifecycle to supervise, a second thing to restart,
and no answer this stream does not already give.

**The gate is `hookprobe.gate`, spawned per tool call.** Not a second copy of
the posture: the same module the Claude adapter calls in-process, reached
through a `hooks.json` this class writes. Codex's PreToolUse payload carries
`tool_name` and `tool_input.command` under exactly the names Claude Code uses,
and a `permissionDecision: deny` stops the tool from running — measured, not
assumed: a denied `echo` produced no `command_execution` item at all.

**Hook trust is bypassed, and that is a boundary worth naming.** Codex asks an
interactive user to approve hook commands once; `exec` has nobody to ask, and
the flag's own help says it is "intended only for automation that already vets
hook sources". This automation does vet it: the home is built here, from a
config template plus a `hooks.json` naming this package. What it does not stop
is anything else that can write into that directory — a process that can put a
file in the run's own workdir can install a hook that runs as the probe.

**Cost is None, always.** Codex reports tokens, never money. The ledger keeps
"nobody counted" and "this was free" apart and the budget breaker reads the
difference, so a plausible-looking 0.0 would corrupt both. Tokens go to `usage`
where the weekly account can price them if it ever learns how.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess  # nosec B404 — spawning the CLI IS this adapter
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import hookprobe
from hookprobe.engine import EngineResult, engine_error, file_fact
from hookprobe.gate import tool_detail
from hookprobe.guard import READONLY
from hookprobe.settings import Settings

logger = logging.getLogger("hookprobe.engine.codex")

# Codex item types that are a tool doing something, and what to call them in the
# process feed. Anything not listed still reaches the feed under its own type —
# an unknown item is news, not noise.
_TOOL_ITEMS = {
    "command_execution": "Bash",
    "file_change": "Edit",
    "mcp_tool_call": "mcp",
    "web_search": "WebSearch",
}


def sandbox_for(bash_guard: str) -> str:
    """The OS sandbox that matches this node's declared posture.

    Belt and braces on purpose, and they are not the same brace. The sandbox is
    the kernel refusing a write; the gate is this node's posture refusing a
    verb. A `readonly` node gets both, so that a gap in the guard's patterns is
    still a process that cannot write, and a runtime that ever forgot to spawn
    the gate is still a process that cannot write.
    """
    return "read-only" if bash_guard == READONLY else "workspace-write"


class CodexEngine:
    """The Codex CLI, driven to satisfy the Runtime Contract."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._workdir = settings.workdir
        self._binary = settings.codex_binary
        self._home = settings.workdir / "codex-home"
        self._proc: asyncio.subprocess.Process | None = None
        # Asked once per process, like the context-usage probe: a check on the
        # per-turn path that spawns an interpreter is a cost paid on every turn
        # forever, and the answer cannot change without a restart.
        self._gate_proven = False

    # ------------------------------------------------------------------ setup

    def prepare_home(self) -> Path:
        """Build the CODEX_HOME this adapter owns, and put the gate in it.

        Owned rather than borrowed: the hook-trust bypass above is only
        defensible while nothing else contributes hooks to the directory the CLI
        reads. The operator's own config is copied in, never written to — their
        credentials stay their file, and a deployment can point at it read-only.
        """
        self._home.mkdir(parents=True, exist_ok=True)
        template = self._settings.codex_config
        if template and template.is_file():
            shutil.copyfile(template, self._home / "config.toml")
        gate = [
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": f"{self._settings.codex_python} -m hookprobe.gate",
                        "timeout": 15,
                    }
                ]
            }
        ]
        # PreToolUse is the gate; PostToolUse is the flight recorder. Subagents
        # get their own events, which is the whole reason the audit can claim to
        # cover calls the message stream never carries.
        hooks = {"PreToolUse": gate, "PostToolUse": gate, "SubagentStart": gate, "SubagentStop": gate}
        (self._home / "hooks.json").write_text(json.dumps({"hooks": hooks}, indent=2), encoding="utf-8")
        return self._home

    def verify_gate(self) -> None:
        """Prove the gate answers before trusting a turn to it, or refuse to run.

        This exists because the first live run of this adapter did not have a
        gate and nothing said so. The hook command could not import hookprobe,
        codex logged the failure and carried on, and a `kubectl delete` ran to
        completion on a node whose /v1/agent was reporting `bash_guard:
        readonly`. Every layer behaved reasonably and the posture was gone.

        So the claim is checked rather than assumed: spawn the gate exactly as
        the runtime will, hand it a call that no posture permits, and require a
        refusal. A node that cannot gate must not run — the note that parked
        this work said an adapter failing this is a finding, not an obstacle,
        and a startup that stops here is that finding being reported.
        """
        if self._gate_proven:
            return
        if shutil.which(self._binary) is None and not Path(self._binary).is_file():
            raise RuntimeError(
                f"HOOKPROBE_RUNTIME=codex but {self._binary!r} is not on this node's PATH. "
                "The adapter drives the CLI as a subprocess; there is nothing to drive."
            )
        probe = {
            "hook_event_name": "PreToolUse",
            # Refused under every posture: no allowlist names it, and an
            # allowlist that did would be naming a server that does not exist.
            "tool_name": "mcp__hookprobe_gate_selftest__probe",
            "tool_input": {},
        }
        try:
            done = subprocess.run(  # nosec B603 — argv is this node's own settings, never model output
                [self._settings.codex_python, "-m", "hookprobe.gate"],
                input=json.dumps(probe),
                capture_output=True,
                text=True,
                env=self._env("probe:gate-selftest"),
                timeout=20,
            )
            decision = json.loads(done.stdout or "{}")
        except Exception as exc:  # noqa: BLE001 — every failure here means the same thing
            raise RuntimeError(f"the tool gate did not answer, so this node cannot hold a posture: {exc}") from exc
        if (decision.get("hookSpecificOutput") or {}).get("permissionDecision") != "deny":
            raise RuntimeError(
                "the tool gate answered but did not refuse a call no posture permits. "
                f"Command: {self._settings.codex_python} -m hookprobe.gate. Answer: {done.stdout.strip()[:200]!r}"
            )
        self._gate_proven = True
        logger.info("tool gate verified: %s -m hookprobe.gate refuses what it must", self._settings.codex_python)

    def _env(self, session_key: str) -> dict[str, str]:
        """What the CLI runs with — and, unchanged, what the gate inherits.

        Verified against Codex: a hook command is spawned with the parent's
        environment, so the gate needs no arguments and no state file to know
        which posture it is enforcing or which session it is recording.
        """
        env = dict(os.environ)
        env["CODEX_HOME"] = str(self._home)
        # The gate IS this package, so the spawned interpreter has to be able to
        # import it. Found out the hard way: without this the hook command died
        # on ModuleNotFoundError, codex carried on, and a `kubectl delete` ran
        # on a node declaring itself read-only. A gate that fails to launch is
        # not a gate, and nothing downstream noticed.
        package_root = str(Path(hookprobe.__file__).resolve().parent.parent)
        env["PYTHONPATH"] = os.pathsep.join(p for p in (package_root, os.environ.get("PYTHONPATH", "")) if p)
        env["HOOKPROBE_SESSION_KEY"] = session_key
        env["HOOKPROBE_GATE_MODE"] = self._settings.bash_guard
        env["HOOKPROBE_GATE_MCP"] = ",".join(sorted(self._settings.mcp_tools))
        env["HOOKPROBE_GATE_AUDIT"] = str(self._workdir / "audit")
        env["HOOKPROBE_GATE_WORKDIR"] = str(self._workdir)
        env["HOOKPROBE_GATE_HOME"] = str(Path.home())
        return env

    # -------------------------------------------------------------------- run

    def _argv(self, resume: str | None) -> list[str]:
        # Options first, then the subcommand: `exec resume` is its own command
        # with its own flag set, and `exec resume <id> --sandbox …` is refused
        # outright — which is how the first resume attempt failed, silently
        # enough that the run came back empty rather than errored.
        argv = [
            self._binary,
            "exec",
            "--json",
            "--sandbox",
            sandbox_for(self._settings.bash_guard),
            # The workdir is a data volume, not a checkout, and codex otherwise
            # refuses to start outside a repository.
            "--skip-git-repo-check",
            "--dangerously-bypass-hook-trust",
            "--cd",
            str(self._workdir),
            "-c",
            f"model={json.dumps(self._settings.model)}",
        ]
        if resume:
            argv += ["resume", resume]
        # The prompt arrives on stdin: an alert body is bounded at 4000 bytes but
        # a follow-up thread is not, and argv is not the place to find out where
        # this platform's limit is.
        argv.append("-")
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
            *self._argv(resume),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._env(session_key),
            cwd=str(self._workdir),
        )
        self._proc = proc
        if proc.stdin is None or proc.stdout is None:
            raise RuntimeError("codex started without the pipes this adapter reads the turn through")
        proc.stdin.write(message.encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()

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
        """Ask the running turn to wind down; False if there was nothing to ask.

        SIGTERM, so the CLI gets to flush the events it has: the difference
        between a stop that records what the turn had done and one that records
        nothing is the whole reason this is in the contract.
        """
        proc = self._proc
        if proc is None or proc.returncode is not None:
            return False
        proc.terminate()
        return True

    def describe_inputs(self, *, resume: str | None = None) -> dict[str, Any]:
        """What this run will actually put in front of the model."""
        return {
            "model": self._settings.model,
            "runtime": "codex",
            "sandbox": sandbox_for(self._settings.bash_guard),
            "home": str(self._home),
            # Codex's own context file, on the volume the agent works in.
            "memory": file_fact(self._workdir / "AGENTS.md"),
            "config": file_fact(self._settings.codex_config) if self._settings.codex_config else None,
            "posture": {"bash_guard": self._settings.bash_guard, "mcp_tools": sorted(self._settings.mcp_tools)},
            "resumed": bool(resume),
        }


class _Turn:
    """One turn's worth of JSONL, folded into an EngineResult.

    Separate from the engine so the conformance suite can drive it over a
    recorded stream: the parsing is the part of an adapter most likely to be
    wrong, and it should not take a paid model run to find out.
    """

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
            logger.debug("codex emitted a line that was not JSON: %s", line[:200])
            return
        if not isinstance(event, dict):
            return
        kind = str(event.get("type") or "")

        if kind == "thread.started":
            found = str(event.get("thread_id") or "")
            if found and found != self.session_id:
                # Obligation four, and the reason it is written down: emitted the
                # moment the runtime says it, not with the result. A first turn
                # killed before its result used to leave nothing to resume.
                self.session_id = found
                self.session_was_first = not self._seen_any
                self._emit({"type": "session", "id": found})
            self._seen_any = True
            return

        self._seen_any = True
        if kind == "item.started":
            self._tool_started(event.get("item") or {})
        elif kind == "item.completed":
            self._item_completed(event.get("item") or {})
        elif kind == "turn.completed":
            usage = event.get("usage")
            if isinstance(usage, dict):
                self.usage = usage
        elif kind == "turn.failed":
            error = event.get("error")
            self.errors.append(str((error or {}).get("message") if isinstance(error, dict) else error or "turn failed"))

    def _tool_started(self, item: dict[str, Any]) -> None:
        name = _TOOL_ITEMS.get(str(item.get("type") or ""))
        if name is None:
            return
        self._emit(
            {
                "type": "tool_use",
                "id": str(item.get("id") or ""),
                "name": name,
                "detail": tool_detail(_tool_input(item)),
            }
        )

    def _item_completed(self, item: dict[str, Any]) -> None:
        kind = str(item.get("type") or "")
        if kind == "agent_message":
            text = str(item.get("text") or "")
            if text:
                self.messages += 1
                self.text = text
                self._emit({"type": "text", "text": text[:500]})
            return
        if kind == "error":
            self.errors.append(str(item.get("message") or "codex reported an error"))
            return
        if kind in _TOOL_ITEMS:
            failed = item.get("exit_code") not in (0, None) or bool(item.get("is_error"))
            self._emit({"type": "tool_done", "id": str(item.get("id") or ""), **({"error": True} if failed else {})})

    def result(self, *, duration_ms: int, returncode: int | None, stderr: str) -> EngineResult:
        error = self._error(returncode, stderr)
        return EngineResult(
            text=self.text,
            message_count=self.messages,
            # Never a number. See the module docstring: codex counts tokens, not
            # money, and a 0.0 here would be a lie the budget breaker believes.
            cost_usd=None,
            error=error,
            session_id=self.session_id,
            usage=self.usage,
            duration_ms=duration_ms,
        )

    def _error(self, returncode: int | None, stderr: str) -> str | None:
        """Why this turn failed, or None — the same question `engine_error` asks.

        Reused rather than re-decided, because `transient()` reads whatever ends
        up here to choose whether to retry, and a second vocabulary of failure
        strings would quietly change which failures get a second chance.
        """
        if returncode not in (0, None):
            tail = stderr.strip().splitlines()[-1] if stderr.strip() else ""
            return f"codex exited {returncode}{': ' + tail if tail else ''}"
        if not self.text:
            first = self.errors[0] if self.errors else "codex produced no answer"
            return engine_error(None, first) or first
        # Text was produced, so the turn answered. Codex has no warning channel
        # and emits notices as `error` items — the hook-trust notice arrives on
        # every run — and the last time an engine's own words were promoted to a
        # failure reason, a $1.68 report was thrown away and replaced with "no
        # analysis was produced". Warnings belong on the feed, not in the verdict.
        return None


def _tool_input(item: dict[str, Any]) -> dict[str, Any]:
    """The item's arguments, under the key `tool_detail` already knows to read."""
    for key in ("command", "path", "query", "url"):
        if item.get(key):
            return {key: item[key]}
    args = item.get("arguments")
    return args if isinstance(args, dict) else {}
