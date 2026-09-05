"""Hand a finished run's report to another node, through the pipe.

The gesture this exists for: an operator reads a plan, decides that one is worth
acting on, and today copies it into an agent running on their own laptop with
every credential they own. That copy IS the approval — it is not missing, it is
just expensive, and what it buys is a run with no audit, no budget and no
declared permissions. This replaces the copy with a click and keeps the decision
exactly where it was.

IT POSTS TO THE PIPE, NOT TO THE OTHER NODE. Sending the report straight to a
work runner's door would be one hop shorter and would leave no record: no event,
no decision trace, no correlation, no dedup. The pipe is the thing in this
family that makes a handover accountable, and a handover that skips it is the
one nobody can reconstruct afterwards. Going through the front door also means
the chain the ledger already joins — watch to plan — simply grows a third hop.

DEDUP IS THE PIPE'S JOB AND IS LEFT TO IT. Two clicks on the same run produce
the same title and body, so the same fingerprint, and the door's dedup stage
records the second as a duplicate instead of buying a second run. Keeping a
local set of already-handed-off keys would be a second copy of a decision the
ledger already holds, and it would not survive a restart.

THE AGENT CANNOT REACH THIS. It is an HTTP route on the service, and the agent's
subprocess does not inherit the service's secrets (see the note of 2026-09-02),
so a curl from inside a run has no token to present. Same division as
MEMORY-SUGGESTION and AI-RULING: the agent proposes in words, the service is
what acts.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from hookprobe.wire import sign_timestamped


class NotConfigured(RuntimeError):
    """No handoff URL. The feature is off, which is the shipping default."""


class NotFinished(RuntimeError):
    """A run still moving has no report to hand over yet."""


def payload_for(run: Any, report: str) -> dict[str, Any]:
    """The event a work runner receives.

    Shaped for the pipe's `title`/`body` extraction, and deliberately flat: the
    receiving door decides what to do with it, and a nested envelope would make
    that config harder to read for no gain.

    `session` carries the plan's key so the work runner can be pointed at the
    case file that produced this — the investigation, not just its conclusion.
    """
    return {
        # Deterministic per run, which is what lets the pipe's dedup catch a
        # second click rather than this file keeping its own memory of one.
        "title": f"plan handed off: {run.session_key}",
        "message": report,
        "session": run.session_key,
        "cost_usd": getattr(run, "cost_usd", 0.0),
    }


def send(url: str, secret: str, run: Any, report: str, timeout: float = 15.0) -> dict[str, Any]:
    """Post it, signed. Raises on anything that means it did not land."""
    if not url:
        raise NotConfigured("HOOKPROBE_HANDOFF_URL is not set; this runner hands nothing off")
    if not report.strip():
        raise NotFinished(f"{run.session_key} has no report to hand over")

    body = json.dumps(payload_for(run, report), ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json", **sign_timestamped(secret, body)}
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 — operator URL  # nosec B310
        raw = response.read().decode("utf-8", "replace")
        status = int(response.status)
    try:
        answer = json.loads(raw)
    except ValueError:
        answer = {"raw": raw[:400]}
    # The pipe's own answer, passed back whole rather than summarised: it says
    # whether the event routed or was skipped as a duplicate, and "you already
    # sent this one" is the thing a second click most needs to be told.
    return {"status": status, "pipe": answer}
