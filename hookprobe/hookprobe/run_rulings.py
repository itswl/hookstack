"""Verdicts a patrol infers about THIS service's own finished runs.

Two kinds of ruling live in this service and they point opposite ways.
`hookprobe.rulings` is what an investigation concludes about a CONDITION; it is
signed and filed with the judge, and it has teeth — a standing `not_worth_it`
answers repeats from the runbook at $0. A run ruling is the other direction:
"was this investigation worth its bill". It gates nothing, spends nothing, and
only fills the worth column of /v1/budget.

The worth column existed with no writer at all until the bulk door was built, and
nobody was going to press 144 buttons; the judge side had already measured that
failure and settled it by inferring the verdict and saying it is an inference.
This does the same for runs, with the same posture as every other marker here:
the patrol PROPOSES lines in its report and holds no credential, and the service
lifts them out and files them. A patrol reads run text, which is downstream of
attacker-influenced alert payloads, so it must not be the thing that writes.

Filed with `ruled_by="patrol:<name>"`, which is what keeps an inference
distinguishable from a person's judgement — see runs.INFERRED_BY_PREFIX. An
inferred verdict also carries its reason, because a verdict nobody can audit is
worth less than no verdict.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger("hookprobe.run_rulings")

# Matches the MARKER, not a well-formed payload, for the same reason AI-RULING
# does: a malformed line must still be stripped, or it rides out to a chat card.
_MARKER = re.compile(r"^[ \t]*RUN-RULING:[ \t]*(.*)$", re.MULTILINE)
RULINGS = ("useful", "useless")
# Higher than AI-RULING's five because the backlog is the point: 144 runs were
# unruled the day the write path landed, and a patrol that can only clear five a
# week never catches up. Still bounded — a report proposing hundreds is a report
# that stopped reading evidence.
_PER_RUN = 20
_WHY_MAX = 400


def extract(text: str) -> tuple[str, list[dict[str, Any]]]:
    """(report without the marker lines, the run rulings worth filing).

    Malformed lines are dropped here rather than filed and refused later: a
    report that says it ruled three and ruled none is the failure worth avoiding,
    and the log that would have said so runs on a schedule nobody watches.
    """
    found: list[dict[str, Any]] = []
    for raw in _MARKER.findall(text or ""):
        try:
            parsed = json.loads(raw)
        except ValueError:
            logger.warning("dropping a RUN-RULING line that is not JSON")
            continue
        if not isinstance(parsed, dict):
            continue
        session_key = str(parsed.get("sessionKey") or "").strip()
        ruling = str(parsed.get("ruling") or "").strip()
        why = str(parsed.get("why") or "").strip()
        if not session_key or ruling not in RULINGS or not why:
            logger.warning("dropping a RUN-RULING for %r: needs sessionKey, a known ruling and a reason", session_key)
            continue
        if any(row["sessionKey"] == session_key for row in found):
            continue  # one verdict per run; the first wins
        found.append({"sessionKey": session_key, "ruling": ruling, "why": why[:_WHY_MAX]})

    if not _MARKER.search(text or ""):
        return text or "", []
    kept = found[:_PER_RUN]
    if kept:
        note = f"_({len(kept)} run ruling{'' if len(kept) == 1 else 's'} filed, marked as inferred.)_"
    else:
        # Every line was malformed. The early return that used to live here left
        # them in place, so the one case the marker-not-JSON pattern exists to
        # cover — `RUN-RULING: not json at all` riding out to a chat card — still
        # happened whenever NOTHING parsed. Strip and say so instead: a note that
        # names the failure is what makes a broken prompt visible.
        note = "_(a run ruling was proposed in a shape this service could not file.)_"
    replaced = False

    def _once(_match: re.Match[str]) -> str:
        nonlocal replaced
        if replaced:
            return ""
        replaced = True
        return note

    stripped = _MARKER.sub(_once, text or "")
    return stripped.rstrip() + ("\n" if text and text.endswith("\n") else ""), kept
