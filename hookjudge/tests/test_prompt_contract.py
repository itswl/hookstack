"""The judge's prompt is versioned, and its safety floor is pinned.

Larkin's discipline made checkable: the prompt has a version, the scenarios
record it plus a hash of the prompt, and a change to the prompt that skips the
re-review fails on the hash. The scenarios are the deterministic floor — what
the rule route must answer when the model is unavailable or the alert carries an
injection — so they run with no provider. scripts/assert_prompt_contract.py runs
the same check in the gate; this keeps it in the unit suite too.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from hookjudge.contract import Incoming
from hookjudge.judge import _SYSTEM_PROMPT, _SYSTEM_PROMPT_VERSION, rule_verdict

SCENARIOS = Path(__file__).resolve().parent.parent / "eval" / "scenarios.jsonl"
_ROWS = [json.loads(x) for x in SCENARIOS.read_text(encoding="utf-8").splitlines() if x.strip()]
_HEADER = next(r for r in _ROWS if r.get("_header"))
_SCENARIOS = [r for r in _ROWS if not r.get("_header")]


def test_the_scenarios_were_reviewed_against_this_prompt_version() -> None:
    assert _HEADER["prompt_version"] == _SYSTEM_PROMPT_VERSION


def test_the_prompt_has_not_changed_since_the_scenarios_were_reviewed() -> None:
    """The forcing function. Edit the prompt without re-stamping and this is what
    goes red, so 'I changed the prompt' can never quietly mean 'and reviewed
    nothing'."""
    current = hashlib.sha256(_SYSTEM_PROMPT.encode("utf-8")).hexdigest()
    assert _HEADER["prompt_sha256"] == current, (
        "the prompt changed; re-review eval/scenarios.jsonl and update its prompt_version and prompt_sha256"
    )


def test_the_safety_floor_holds_offline() -> None:
    """Every scenario, against the deterministic rule route. An injection can
    only raise attention; a drill stays low even when it names payment; a real
    outage floors high; a routine threshold is not forced up."""
    for s in _SCENARIOS:
        alert = s["alert"]
        event = Incoming.parse(
            {k: alert.get(k, "") for k in ("source", "title", "body", "level")} | {"fields": alert.get("fields") or {}},
            now=time.time(),
        )
        got = (rule_verdict(event).importance or "").lower()
        want = [v.lower() for v in s["expect"]["importance"]]
        assert got in want, f"{s['id']}: {got!r} not in {want} — {s.get('why', '')}"
