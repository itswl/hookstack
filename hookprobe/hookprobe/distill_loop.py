"""What a finished run leaves for the next one — the loop, closed from outside.

Three decisions that all happen after the report is written and before the run
settles. They are one story told in stages, which is why they sit together:

* `auto_distill` — if the operator set a cap, assemble a runbook out of the
  run's own record and write it. From the SERVICE, never through the agent's
  tools; hookprobe.distill and hookprobe.inputs explain at length why those are
  different acts, and this module is on the safe side of that line.
* `maybe_consolidate` — once a runbook has accumulated enough cases, spend one
  agent run turning the pile into a curated procedure. Spawned from the
  completion path, so it never delays the report somebody is waiting for.
* `accept_consolidation` — that run's product is a PROPOSAL beside the manifest,
  waiting for a person. A consolidation run is never itself distilled, or the
  loop would start writing runbooks about how to rewrite runbooks.

Every one of them is best-effort by construction. The report is already written
and somebody is waiting for it, so a failure in here is logged and dropped: the
outcome of an investigation is not the learning loop's to lose. What each stage
did lands on `run.distilled` rather than only in the log, because "it silently
did nothing again" is the failure this whole loop exists to end.
"""

from __future__ import annotations

import contextlib
import json
import logging
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from hookprobe import distill
from hookprobe.engine import EngineResult
from hookprobe.files import atomic_write
from hookprobe.runs import Run, RunStore
from hookprobe.settings import Settings

logger = logging.getLogger("hookprobe.distill_loop")


def auto_distill(run: Run, result: EngineResult, settings: Settings) -> None:
    """Leave the next investigation a runbook, if the operator asked for it.

    The write happens in the service and never through the agent's tools — that
    separation is the whole reason the input guard can stay on while the loop
    closes. Failure is never the run's problem: the report is already written and
    somebody is waiting for it.
    """
    if settings.auto_distill_max <= 0:
        return
    try:
        outcome = distill.auto_write(
            run,
            skills_dir=settings.workdir / ".claude" / "skills",
            limit=settings.auto_distill_max,
            input_changes=result.input_changes,
        )
    except Exception:  # noqa: BLE001 — distilling must never cost a finished report
        logger.exception("auto-distill failed session=%s", run.session_key)
        return
    run.distilled = outcome
    if "skipped" in outcome:
        logger.info("auto-distill skipped session=%s reason=%s", run.session_key, outcome["skipped"])
    else:
        verb, name = next(iter(outcome.items()))
        logger.info("auto-distill %s session=%s runbook=%s", verb, run.session_key, name)


ATTEMPT_FILE = "consolidation-attempt.json"


def _record_attempt(skill_dir: Path, name: str, reason: str) -> None:
    """Remember that a consolidation was paid for and produced nothing.

    Without this the loop has no memory of failure and re-arms on the ABSENCE of
    proposal.md — so a draft that never validates spawns another paid run on
    every later investigation of that alert, forever, and this path is outside
    the budget breaker by design because an operator set the threshold. One
    unluckily-worded runbook was an unbounded bill.
    """
    try:
        cases = distill.case_count((skill_dir / "SKILL.md").read_text(encoding="utf-8"))
    except OSError:
        cases = 0
    with contextlib.suppress(OSError):
        atomic_write(
            skill_dir / ATTEMPT_FILE,
            json.dumps({"at": round(time.time(), 3), "cases": cases, "reason": reason}, ensure_ascii=False).encode(),
        )


def _attempt_blocks(skill_dir: Path, count: int, threshold: int) -> bool:
    """True when the last attempt failed and not enough has changed to retry.

    Retrying at all is deliberate — a write that failed on a full disk should not
    disable consolidation for a runbook permanently — but it retries at the rate
    the feature was designed for: one run per `threshold` NEW cases. So a runbook
    that cannot be consolidated costs the same as one that can, instead of one
    run per investigation.
    """
    try:
        raw = json.loads((skill_dir / ATTEMPT_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(raw, dict):
        return False
    return count < int(raw.get("cases") or 0) + threshold


def maybe_consolidate(run: Run, settings: Settings, store: RunStore, start: Callable[..., Run]) -> None:
    """At the case threshold, spend one run turning the pile into a draft.

    Called from the completion path, so it never delays the report; the draft
    lands as proposal.md and waits for review. Deliberately outside the
    event-door budget breaker (an operator set the threshold, so this spend was
    asked for), but every run is recorded, so the window spend and the board
    both show it.
    """
    threshold = settings.consolidate_at
    if threshold <= 0:
        return
    name = (run.distilled or {}).get("updated") or (run.distilled or {}).get("installed")
    if not name:
        return
    skill_dir = settings.workdir / ".claude" / "skills" / str(name)
    manifest = skill_dir / "SKILL.md"
    if (skill_dir / "proposal.md").exists():
        return  # one open proposal at a time; approving or rejecting re-arms
    try:
        count = distill.case_count(manifest.read_text(encoding="utf-8"))
    except OSError:
        return
    if count < threshold:
        return
    if _attempt_blocks(skill_dir, count, threshold):
        return  # the last one was paid for and produced nothing; wait for more cases
    for other in store.list_runs(limit=50):
        if not other.finished and other.meta.get("consolidates") == name:
            return  # already being consolidated
    message = distill.CONSOLIDATION_MESSAGE.format(path=str(manifest), count=count, name=name)
    payload: dict[str, Any] = {
        "message": message,
        "sessionKey": f"consolidate:{name}:{uuid.uuid4().hex[:6]}",
        "_meta": {"consolidates": str(name), "title": f"consolidate: {name}"},
    }
    consolidation = start(payload, origin="system")
    logger.info("consolidation spawned session=%s runbook=%s cases=%s", consolidation.session_key, name, count)


def accept_consolidation(run: Run, result: EngineResult, settings: Settings) -> None:
    """Park the draft as a proposal — or say exactly why not."""
    name = str(run.meta.get("consolidates") or "")
    skill_dir = settings.workdir / ".claude" / "skills" / name
    if not (skill_dir / "SKILL.md").is_file():
        run.distilled = {"skipped": f"runbook '{name}' vanished mid-consolidation"}
        _record_attempt(skill_dir, name, f"runbook '{name}' vanished mid-consolidation")
        return
    if result.input_changes:
        run.distilled = {"skipped": "run changed its own inputs"}
        _record_attempt(skill_dir, name, "run changed its own inputs")
        return
    draft = distill.valid_consolidation(result.text or "", name)
    if not draft:
        run.distilled = {"skipped": "draft did not validate as a manifest"}
        logger.warning("consolidation draft rejected session=%s runbook=%s", run.session_key, name)
        _record_attempt(skill_dir, name, "draft did not validate as a manifest")
        return
    try:
        # APPLIED, not parked. It used to land as proposal.md and wait for a
        # person, and on a deployment where nobody answers that is where it
        # stayed: one draft sat unaccepted from 2026-08-19 onward while the
        # threshold refused to re-arm behind it.
        #
        # The gate did not survive being looked at. `auto_write` installs and
        # updates SKILL.md with no human at all, and SKILL.md is loaded as
        # INSTRUCTION by every later run — model-authored conclusions included.
        # So a gate on CONSOLIDATING those same files was guarding against a
        # class of text already arriving unguarded through the front door. It
        # bought no safety; it bought a stalled loop.
        #
        # What replaces it is reversibility, which is worth more here than
        # permission: the displaced version is snapshotted first and
        # POST /v1/skills/{name}/history/{stamp}/restore puts it back in one
        # call. And `reviewed` stays False, so the skills page can still say
        # nobody has read this — it just no longer waits for them.
        distill.apply_consolidation(skill_dir, draft, by="service", at=time.time())
    except OSError as exc:
        run.distilled = {"skipped": f"write failed: {exc}"}
        _record_attempt(skill_dir, name, f"write failed: {exc}")
        return
    with contextlib.suppress(OSError):
        (skill_dir / ATTEMPT_FILE).unlink(missing_ok=True)
    run.distilled = {"consolidated": name}
    logger.info("consolidation applied session=%s runbook=%s", run.session_key, name)
