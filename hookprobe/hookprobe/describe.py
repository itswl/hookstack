"""What this agent says it can be asked to do, in ANP's dialect.

`GET /v1/agent` answers "what IS this node" for a human reading a console:
name, runtime, model, workspace, policy, health. This module answers a
different question for a different reader — "what can I ask this agent, and
what will it refuse without a person" — as an Agent Description document, the
shape an ANP crawler reads (`.agents/notes/proposed/2026-09-09-anp-evaluated…`).

**A dialect at the edge, not a dependency.** The evaluation that produced this
rejected ANP's identity layer: verifying a DID-WBA request resolves the DID
document over HTTPS from the DID's own hostname, hard-coded, and every node here
is loopback-bound. None of that is needed to be *described*. This module emits
JSON in a documented shape and imports nothing; the vocabulary was read off the
spec's own published example rather than guessed.

**It deliberately says less than `/v1/agent`.** No model, no gateway endpoint,
no workspace path, no budget. Those answer "how is this node configured", which
is the operator's business and in one case an estate identifier; a description
answers "what can I ask for". A document meant to be crawlable is the wrong
place to widen what a caller learns.

**`humanAuthorization` is the field worth having.** ANP's interface vocabulary
carries it as a first-class boolean, which is this stack's central claim
expressed in somebody else's schema: a caller can see, before calling, which
doors stop for a person.
"""

from __future__ import annotations

import time
from typing import Any

from hookprobe.settings import Settings

# Read from the spec's own published example (examples/adp/lkcoffe/ad.json) —
# `protocolType`/`protocolVersion` at the top, `type`/`protocol`/`url` per
# interface. The SDK's crawler reads a slightly different set of names than the
# spec example uses, so only fields that appear in BOTH are emitted.
_PROTOCOL = "ANP"
_PROTOCOL_VERSION = "1.0.0"

# What a caller may ask this node for, and how. Paths only: the absolute origin
# is the operator's to state, because a node bound to loopback does not know it.
_INTERFACES: tuple[dict[str, Any], ...] = (
    {
        "type": "NaturalLanguageInterface",
        "path": "/hooks/agent",
        "method": "POST",
        "security": "bearer_sc",
        "description": ("Ask for an investigation as a finished prompt. Answers a run id; poll the report below."),
    },
    {
        "type": "StructuredInterface",
        "path": "/hooks/event",
        "method": "POST",
        "security": "hmac_sc",
        "description": (
            "A normalized event — title, message, level, source. Signed per source; "
            "this door starts paid work, so an unsigned request is refused."
        ),
    },
    {
        "type": "StructuredInterface",
        "path": "/sessions/{session_key}/final",
        "method": "GET",
        "security": "bearer_sc",
        "description": "Poll one investigation's report: 202 while it runs, the text once it is done.",
    },
    {
        "type": "NaturalLanguageInterface",
        "path": "/sessions/{session_key}/continue",
        "method": "POST",
        "security": "bearer_sc",
        "description": "A follow-up question in a finished investigation, continuing its session.",
    },
    {
        "type": "StructuredInterface",
        "path": "/v1/remediations/{proposal_id}/approve",
        "method": "POST",
        "security": "bearer_sc",
        # The reason this module exists in ANP's dialect rather than a private
        # one: a caller can see which doors will not move without a person.
        "humanAuthorization": True,
        "description": (
            "Approve a proposed remediation. Nothing here executes without this call, "
            "what it may then run is bounded by an operator's allowlist, and a target "
            "another procedure has just acted on is refused until its cooldown passes."
        ),
    },
)


def agent_description(settings: Settings, *, version: str, now: float | None = None) -> dict[str, Any]:
    """The Agent Description document for this node. Pure.

    `HOOKPROBE_PUBLIC_URL` decides whether the interfaces carry absolute URLs.
    Unset — the default, and true of every deployment in this repository — and
    they carry paths, with `reachable: false` saying so rather than a caller
    being handed a loopback address that resolves to their own machine. Same
    rule the budget ceiling learned today: do not print what was not measured.
    """
    base = settings.public_url.rstrip("/") if settings.public_url else ""
    document: dict[str, Any] = {
        "protocolType": _PROTOCOL,
        "protocolVersion": _PROTOCOL_VERSION,
        "type": "AgentDescription",
        "name": settings.agent_name,
        "description": settings.agent_role or f"a hookstack investigator, posture {settings.bash_guard}",
        "version": version,
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now if now is not None else time.time())),
        "reachable": bool(base),
        "securityDefinitions": {
            "bearer_sc": {"scheme": "bearer", "in": "header", "name": "Authorization"},
            "hmac_sc": {"scheme": "hmac-sha256", "in": "header", "name": "X-Hook-Signature"},
        },
        "interfaces": [],
    }
    if base:
        document["url"] = f"{base}/v1/agent/description"
    for entry in _INTERFACES:
        interface = {k: v for k, v in entry.items() if k != "path"}
        interface["protocol"] = "http"
        interface["version"] = _PROTOCOL_VERSION
        interface["url"] = f"{base}{entry['path']}" if base else entry["path"]
        document["interfaces"].append(interface)
    return document
