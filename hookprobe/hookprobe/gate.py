"""The posture as one decision, and as a command a runtime can spawn.

This node's read-only claim is not a prompt. It is a gate that runs BEFORE a
tool does, and the Runtime Contract on `service.Engine` names it first among
the obligations a second adapter has to meet.

The two runtimes meet it by different mechanisms. The Claude adapter registers
in-process `PreToolUse` hooks; Codex spawns a command per tool call and reads
its answer from stdout. The mechanisms differ and that is fine. What must not
differ is the DECISION, because the moment it is written twice the word
`readonly` means one thing on a deployment running one engine and something
slightly else on a deployment running the other, and nothing in either test
suite would notice the drift.

So the decision lives here once, as a pure function, and both mechanisms call
it. `python -m hookprobe.gate` is the same function reading one hook payload on
stdin and writing one decision on stdout, which is the shape Claude Code and
Codex happen to share.

This module is deliberately cheap to import: `guard`, `inputs`, `redact` and
the standard library, and nothing that reaches for an SDK. It is spawned once
per tool call, so an import that costs half a second costs it on every tool the
agent runs.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

from hookprobe import inputs
from hookprobe.guard import READONLY, bash_deny_reason
from hookprobe.redact import redact

# Tools that put bytes on disk. NotebookEdit and MultiEdit are not in the
# engine's allowlist today; naming them costs nothing and means enabling one
# later cannot quietly reopen the hole.
WRITE_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})
_WRITE_PATH_KEYS = ("file_path", "notebook_path", "path")


def tool_detail(tool_input: Any) -> str:
    """One line saying what a tool call is about, for the live process feed.

    Redacted HERE rather than at the sinks, because this one string is the most
    copied in the service: it reaches the run's event feed and `results/*.json`,
    the flight recorder's `audit/*.jsonl`, and — via distill — the case block of
    a generated SKILL.md that every later run loads and /v1/skills serves. Three
    sinks today and a fourth one feature away; masking at each of them is
    masking the next one leaks around. See hookprobe/redact.py for what it does
    and does not catch.
    """
    data = tool_input if isinstance(tool_input, dict) else {}
    for key in ("command", "file_path", "pattern", "query", "url", "path", "skill", "description"):
        value = data.get(key)
        if value:
            return redact(str(value))[:300]
    try:
        return redact(json.dumps(data, ensure_ascii=False))[:200]
    except (TypeError, ValueError):
        return ""


def append_audit(audit_dir: Path, line: dict[str, Any]) -> None:
    """One JSONL line into today's audit file. Never raises; see the callers."""
    audit_dir.mkdir(parents=True, exist_ok=True)
    day_file = audit_dir / (time.strftime("%Y-%m-%d") + ".jsonl")
    with day_file.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(line, ensure_ascii=False) + "\n")


def mcp_deny_reason(tool_name: str, allowed: frozenset[str]) -> str | None:
    """Why this MCP tool may not run, or None to let it through.

    Mounting an MCP server is plumbing; deciding what the agent may DO with it
    is policy, and this is the component that reads attacker-influenced text —
    so the two are separate settings and this one is closed by default. There is
    no such thing as a read-only server: a chat server ships `send_message` beside
    `search_chat_records`, so without a tool-level gate, "let the planner read
    the thread" and "let a message in that thread post as the operator" were the
    same mount.

    Two forms, both exact: the full `mcp__server__tool`, or `mcp__server__*` for
    a whole server. No general globbing — a pattern language here would be a
    second thing to get subtly wrong, and the list is meant to be read by
    somebody deciding what an agent may do on their behalf.

    Non-MCP tools are not this guard's business; `_ALLOWED_TOOLS` and the bash
    and input guards already answer for those.
    """
    if not tool_name.startswith("mcp__"):
        return None
    if tool_name in allowed:
        return None
    server = tool_name.split("__")[1] if tool_name.count("__") >= 2 else ""
    if server and f"mcp__{server}__*" in allowed:
        return None
    if not allowed:
        return (
            f"{tool_name} refused: no MCP tool may run until HOOKPROBE_MCP_TOOLS names one. "
            "Mounting a server does not grant its tools — list exactly the ones this instance "
            "may call (or mcp__<server>__* for all of them), and remember that whoever can put "
            "text in front of this agent can ask it to use every tool on that list."
        )
    return (
        f"{tool_name} refused: not in HOOKPROBE_MCP_TOOLS. This instance may call "
        f"{', '.join(sorted(allowed))} and nothing else."
    )


# `*** Update File: path`, and the three siblings codex's patch envelope uses.
_PATCH_TARGET = re.compile(r"^\*\*\* (?:Update|Add|Delete) File: (.+)$|^\*\*\* Move to: (.+)$", re.MULTILINE)
# A shell segment that puts bytes into a named file: a redirect, `tee`, or an
# in-place edit. Deliberately not a shell parser — see _shell_write_target.
_REDIRECT = re.compile(r">>?\s*['\"]?([^\s'\"|;&>]+)")


def patch_targets(command: str) -> list[str]:
    """Every path a codex `apply_patch` envelope would write.

    Codex does not edit through a tool with a `file_path` argument. It sends one
    `apply_patch` call whose whole patch is a string, and the paths are lines
    inside it — so a guard that reads argument keys sees a tool it does not
    recognise carrying no path at all, and lets it through. Measured: an
    `apply_patch` rewriting CLAUDE.md reached this gate as
    `tool_name='apply_patch'` with `tool_input={'command': '*** Begin Patch…'}`.
    """
    found: list[str] = []
    for update, move in _PATCH_TARGET.findall(command or ""):
        target = (update or move).strip()
        if target:
            found.append(target)
    return found


def shell_write_target(command: str, roots: tuple[Path, ...], workdir: Path | None) -> str | None:
    """A protected path this shell command puts bytes into, or None.

    The input guard is tool-shaped: it reads the arguments of Write and Edit. A
    shell redirect goes straight around it, and did on every runtime and both
    postures — `printf 'x' >> CLAUDE.md` was allowed by the bash guard and never
    reached the input guard, because it is a Bash call and Bash has no path
    argument to inspect.

    Two shapes are checked. A redirect or a `tee`, where the target follows the
    operator; and a segment whose command writes a file named in its arguments
    (`sed -i`, `cp`, `mv`, `install`, `dd`), where every argument is a
    candidate. Segments are split on the shell's own separators so a target in
    one does not answer for another.

    This closes the obvious forms and not a determined adversary, which is the
    same honesty the bash guard's rules are written with: a redirect assembled
    at runtime out of two variables gets through. It stops the over-eager model,
    which is the case that actually happens.
    """
    for segment in re.split(r"[|;&\n]+", command or ""):
        words = segment.split()
        candidates = [target for target in _REDIRECT.findall(segment)]
        if words and _writes_a_named_file(words):
            candidates += [word for word in words[1:] if not word.startswith("-")]
        for candidate in candidates:
            hit = candidate.strip("\"'")
            if not hit:
                continue
            target = Path(hit)
            if not target.is_absolute() and workdir is not None:
                target = workdir / target
            resolved = _resolve_quietly(target)
            for root in roots:
                if resolved == root or root in resolved.parents:
                    return hit
    return None


def _writes_a_named_file(words: list[str]) -> bool:
    """Commands that write a path given as an argument rather than a redirect."""
    head = Path(words[0]).name
    if head in ("cp", "mv", "install", "dd", "truncate", "tee"):
        return True
    # `sed` only writes with -i, and BSD's takes a separate suffix argument, so
    # the target is not at a fixed position. Every argument is a candidate.
    return head == "sed" and any(word.startswith("-i") or word == "--in-place" for word in words)


def _resolve_quietly(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path


def deny_reason(
    tool_name: str,
    tool_input: Any,
    *,
    bash_mode: str = READONLY,
    mcp_allowed: frozenset[str] = frozenset(),
    workdir: Path | None = None,
    home: Path | None = None,
) -> tuple[str, str, str] | None:
    """Which guard refuses this call, why, and what to record — or None to allow.

    Returns `(guard, reason, detail)`. The three guards answer for disjoint sets
    of tools, so the order below is presentation rather than precedence.
    """
    name = str(tool_name or "")
    data = tool_input if isinstance(tool_input, dict) else {}

    if name == "Bash":
        command = str(data.get("command") or "")
        reason = bash_deny_reason(command, bash_mode)
        if reason is not None:
            return ("bash", reason, command[:300])
        if workdir is not None:
            hit = shell_write_target(command, inputs.protected_paths(workdir, home), workdir)
            if hit is not None:
                return (
                    "input",
                    f"input guard: this command writes {hit}, which steers the next run. "
                    "A shell redirect is not a way around the guard on the edit tools.",
                    command[:300],
                )

    # Codex sends one `apply_patch` call whose paths live inside the patch text
    # rather than in an argument, so the key-reading branch below cannot see it.
    if name == "apply_patch" and workdir is not None:
        for target in patch_targets(str(data.get("command") or data.get("input") or "")):
            reason = inputs.write_deny_reason(target, workdir=workdir, home=home)
            if reason is not None:
                return ("input", reason, target[:300])

    if name.startswith("mcp__"):
        reason = mcp_deny_reason(name, mcp_allowed)
        if reason is not None:
            return ("mcp", reason, "")

    if name in WRITE_TOOLS and workdir is not None:
        for key in _WRITE_PATH_KEYS:
            target = str(data.get(key) or "")
            reason = inputs.write_deny_reason(target, workdir=workdir, home=home)
            if reason is not None:
                return ("input", reason, target[:300])

    return None


# Shapes that are a credential and cannot plausibly be anything else. Borrowed
# in idea from `ToolOutputGuardrail` in openai/openai-agents-python: this stack
# guarded tool INPUT and only recorded tool output, and the two are not the same
# question.
#
# The gap is not theoretical and it is not a hole in the input guard — it is the
# posture working as designed. `readonly` refuses CHANGES, so
# `kubectl get secret db-creds -o yaml` and `env` are both ALLOWED, and they
# should be: an investigator that cannot read cannot investigate. What was
# missing is that nothing then looked at what came back. A run whose context
# was filled with a live credential was indistinguishable from one that read a
# pod list, which means nobody could decide to discard the report or rotate the
# key.
#
# Deliberately narrow. A guard that fires on ordinary output is a guard people
# learn to ignore, so this matches only prefixes and headers that are issued
# credentials — never "password" or "secret" as words, which appear in every
# other line of a Kubernetes manifest.
_SECRET_SHAPES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private key", re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----")),
    ("aws access key id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("openai-style api key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("json web token", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
)

# How much of an answer is examined. Tool output can be a megabyte of logs and
# this runs on every call, including the spawned path that pays an interpreter
# start each time. A credential that appears only past 64 KB of output is a case
# this accepts missing.
_OUTPUT_SCAN_LIMIT = 65_536


def output_reason(tool_response: Any) -> str | None:
    """What kind of credential this tool output appears to contain, or None.

    Names the KIND, never the match. The reason string travels into the audit
    file and, on some deployments, into a chat card — so a guard that quoted the
    secret it found would be the leak it exists to record.
    """
    if isinstance(tool_response, dict):
        text = " ".join(str(v) for v in tool_response.values() if isinstance(v, str | int | float))
    elif isinstance(tool_response, str | bytes):
        text = tool_response.decode("utf-8", "replace") if isinstance(tool_response, bytes) else tool_response
    else:
        return None
    found = [name for name, pattern in _SECRET_SHAPES if pattern.search(text[:_OUTPUT_SCAN_LIMIT])]
    return ", ".join(sorted(set(found))) if found else None


def refusal(reason: str) -> dict[str, Any]:
    """A PreToolUse denial, in the shape Claude Code and Codex both read."""
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def _env_path(name: str) -> Path | None:
    raw = os.environ.get(name, "").strip()
    return Path(raw) if raw else None


def decide(payload: dict[str, Any], env: dict[str, str]) -> dict[str, Any]:
    """One hook payload in, one decision out. The whole of the spawned gate.

    Configuration arrives through the environment because that is what a spawned
    hook actually inherits — verified against Codex, which passes the parent's
    environment to hook commands unchanged. Nothing here is read from the
    payload except the call being judged: a runtime that could talk this process
    into a wider posture would be a gate the agent can argue with.
    """
    event = str(payload.get("hook_event_name") or "")
    # Which run this tool call belongs to, so the flight recorder can say. Set
    # by the adapter when it spawns the runtime, never by an operator.
    session = env.get("HOOKPROBE_SESSION_KEY", "") or str(payload.get("session_id") or "")
    # Where the flight recorder writes. Absent means no audit, which is a
    # decision the adapter makes and not a default anything drifts into.
    audit_dir = Path(env["HOOKPROBE_GATE_AUDIT"]) if env.get("HOOKPROBE_GATE_AUDIT") else None
    tool_name = str(payload.get("tool_name") or "")
    tool_input = payload.get("tool_input")

    if event == "PreToolUse":
        verdict = deny_reason(
            tool_name,
            tool_input,
            # The posture this call is judged against, passed by the adapter
            # from `bash_guard`. Nothing in the payload can widen it.
            bash_mode=env.get("HOOKPROBE_GATE_MODE", READONLY),
            # The MCP tools this node may call, from `mcp_tools`. Empty means
            # none, which is the closed default the guard is built around.
            mcp_allowed=frozenset(t for t in env.get("HOOKPROBE_GATE_MCP", "").split(",") if t),
            # The run's own volume, so the input guard knows which files steer
            # the next run and may not be written by this one.
            workdir=_env_path("HOOKPROBE_GATE_WORKDIR"),
            # The agent's home, whose settings and skills are inputs too.
            home=_env_path("HOOKPROBE_GATE_HOME"),
        )
        if verdict is None:
            return {}
        which, reason, detail = verdict
        if audit_dir is not None:
            append_audit(
                audit_dir,
                {
                    "ts": round(time.time(), 3),
                    "session": session,
                    "tool": tool_name,
                    "detail": redact(detail),
                    "denied": True,
                    "guard": which,
                    "reason": reason,
                },
            )
        return refusal(reason)

    if event == "PostToolUse" and audit_dir is not None:
        response = payload.get("tool_response")
        line = {
            "ts": round(time.time(), 3),
            "session": session,
            "tool": tool_name,
            "detail": tool_detail(tool_input),
            "error": bool(response.get("is_error")) if isinstance(response, dict) else False,
        }
        # What came BACK, not what went in. Recorded rather than blocked: the
        # output has already reached the model by the time a PostToolUse hook
        # runs, so a "deny" here would be theatre — and the shape that would
        # make a runtime withhold it is not one all three adapters agree on.
        # What this buys is that the contamination is now a fact somebody can
        # act on: discard the report, rotate the key, or both.
        leaked = output_reason(response)
        if leaked:
            line["output_secret"] = leaked
        append_audit(audit_dir, line)
    return {}


# The service's own secrets, blanked out of everything an agent's subprocess can
# read. Decided 2026-09-02 (`.agents/notes/implemented/`) after the production
# compose was found injecting the whole deployment `.env` into the container: a
# Bash step, or an injected instruction that reaches one, could read the pipe's
# HMAC keys — which forge a signed event — and the chat app's credentials, which
# post as the bot. None of these are provider credentials, so blanking them
# costs no adapter its model.
#
# It lives HERE, next to the environment every spawned runtime is built from,
# because it did not: the decision was implemented as a private tuple on the
# Claude adapter, and when the codex and pi adapters arrived they built their
# env from `dict(os.environ)` through this function and inherited every one of
# them. The boundary held on the adapter it was written for and was simply
# absent on the two that came later — the same shape as the posture that meant
# one thing per engine, and the reason that decision now lives in one module.
SECRETS_WITHHELD_FROM_AGENT = (
    "HOOKPROBE_EVENT_SECRET",
    "HOOKPROBE_RETURN_SECRET",
    "HOOKPROBE_RULING_SECRET",
    "LARK_APP_ID",
    "LARK_APP_SECRET",
    "LARK_CHAT_ID",
    "SHADOW_INGEST_SECRET",
    "SHADOW_ADMIN_TOKEN",
    "SHADOW_READ_TOKEN",
    "SHADOW_ACTION_SECRET",
    "SHADOW_RULING_SECRET",
    "SHADOW_RETURN_URL",
    "WW_RELAY_SECRET",
)


def environment(settings: Any, session_key: str, *, package_root: str) -> dict[str, str]:
    """The environment a spawned runtime passes down to this gate.

    Shared by every adapter that reaches the gate by spawning it, because the
    alternative is each of them deciding separately what the gate is allowed to
    know — and one of them getting it slightly wrong is a node holding a posture
    nobody asked for. The same argument applies to what the runtime may READ,
    which is why the withholding below is here and not in a caller.
    """
    env = dict(os.environ)
    # Overridden to "" rather than deleted: a spawned process inherits the
    # parent's environment, and an empty value is what a door treats as
    # unconfigured — which is refused rather than open.
    for name in SECRETS_WITHHELD_FROM_AGENT:
        env[name] = ""
    # The gate IS this package, so the interpreter spawning it has to be able to
    # import it. Learned the hard way: without this the hook died on
    # ModuleNotFoundError, the runtime carried on, and a `kubectl delete` ran on
    # a node declaring itself read-only.
    env["PYTHONPATH"] = os.pathsep.join(p for p in (package_root, os.environ.get("PYTHONPATH", "")) if p)
    env["HOOKPROBE_SESSION_KEY"] = session_key
    env["HOOKPROBE_GATE_MODE"] = settings.bash_guard
    env["HOOKPROBE_GATE_MCP"] = ",".join(sorted(settings.mcp_tools))
    env["HOOKPROBE_GATE_AUDIT"] = str(settings.workdir / "audit")
    env["HOOKPROBE_GATE_WORKDIR"] = str(settings.workdir)
    env["HOOKPROBE_GATE_HOME"] = str(Path.home())
    return env


# Refused under every posture: no allowlist names it, and an allowlist that did
# would be naming a server that does not exist.
SELFTEST_TOOL = "mcp__hookprobe_gate_selftest__probe"


def verify(python: str, env: dict[str, str]) -> None:
    """Prove the gate answers before a turn is trusted to it, or raise.

    This exists because the first live run of a spawned-gate adapter had no gate
    and nothing said so. The hook command could not import hookprobe, the
    runtime logged it and carried on, and a `kubectl delete` ran to completion
    on a node whose /v1/agent said `bash_guard: readonly`. Every layer behaved
    reasonably and the posture was simply absent.

    So the claim is checked rather than assumed: spawn the gate exactly as the
    runtime will, hand it a call that no posture permits, and require a refusal.
    A node that cannot gate must not run.
    """
    import subprocess  # nosec B404 — spawning the gate is the point

    probe = {"hook_event_name": "PreToolUse", "tool_name": SELFTEST_TOOL, "tool_input": {}}
    try:
        done = subprocess.run(  # nosec B603 — argv is this node's own settings, never model output
            [python, "-m", "hookprobe.gate"],
            input=json.dumps(probe),
            capture_output=True,
            text=True,
            env=env,
            timeout=20,
        )
        decision = json.loads(done.stdout or "{}")
    except Exception as exc:  # noqa: BLE001 — every failure here means the same thing
        raise RuntimeError(f"the tool gate did not answer, so this node cannot hold a posture: {exc}") from exc
    if (decision.get("hookSpecificOutput") or {}).get("permissionDecision") != "deny":
        raise RuntimeError(
            "the tool gate answered but did not refuse a call no posture permits. "
            f"Command: {python} -m hookprobe.gate. Answer: {done.stdout.strip()[:200]!r}"
        )


def consulted(audit_dir: Path, session_key: str, *, since: float) -> bool:
    """Did the gate actually record anything for this session during this turn?

    `verify()` proves the gate ANSWERS. It does not prove the runtime ASKS, and
    those are different claims that look identical from inside this process.
    The difference was measured: driving codex through its app-server leaves a
    perfectly working gate command sitting unused, so `verify()` passes, the
    posture is reported, and `kubectl delete` runs. Nothing anywhere noticed.

    So a turn that ran tools and left no line here is a turn that ran ungated,
    and the caller treats that as a broken node rather than a quiet oddity.
    """
    # The recorder stamps `round(time.time(), 3)`, which can land a hair BELOW
    # the caller's unrounded start. Half a millisecond of slack, so a line
    # written the instant the turn began still counts as written during it.
    since -= 0.001
    day_file = audit_dir / (time.strftime("%Y-%m-%d") + ".jsonl")
    try:
        with day_file.open(encoding="utf-8") as handle:
            for raw in handle:
                try:
                    line = json.loads(raw)
                except ValueError:
                    continue
                if line.get("session") == session_key and float(line.get("ts") or 0) >= since:
                    return True
    except OSError:
        return False
    return False


def main() -> int:
    """Read one payload, print one decision.

    Fails CLOSED, and only on the pre-tool path: if this process cannot reach a
    decision — bad JSON, an unreadable audit directory, a bug — the tool does
    not run. The alternative is a gate whose failure mode is silently becoming
    permissive, which is the failure mode you would never find out about.
    """
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw or "{}")
        if not isinstance(payload, dict):
            raise ValueError("hook payload was not an object")
        print(json.dumps(decide(payload, dict(os.environ)), ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001 — see the docstring: this is the fail-closed path
        if '"PreToolUse"' in raw or "'PreToolUse'" in raw:
            print(json.dumps(refusal(f"the tool gate could not reach a decision: {exc}")))
        else:
            print("{}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
