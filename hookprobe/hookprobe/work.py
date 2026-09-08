"""WorkItem — the unit a person tracks, derived from what the loop already keeps.

A run is one execution attempt; a work item is the thing somebody wants
finished. Most of the time the two are one object seen from two sides: one
alert, one investigation, one report. They come apart in exactly three places,
and those three are why this module exists:

  * a re-fire or a chat follow-up adds turns to a run that is already the same
    piece of work, so a board counting runs would say the work happened twice;
  * a plan handed to a work node is TWO runs on two services, and nobody
    tracking it cares which node holds which half;
  * what a person still owes — an approval, a memory line, a ruling — is
    recorded beside the run rather than on it, so "what is blocked" was a
    question only a human could answer by opening three pages.

**Derived, not stored, and that is deliberate.** Every fact below is already
written down somewhere durable: the run record, the proposal file, the
suggestion queue. A second copy would be a second thing to keep true, and the
first divergence between them would be invisible. The price is that a work item
cannot carry what the loop does not already know — no free-text goal, no
assignee, no SLA. When one of those earns its place it gets a file of its own
and this module reads it too; until then their absence is honest.

The identity is `work_id`, resolved by the event door (events.py) in this
order: a `work_id` an upstream node stated, else the correlation the pipe put
on the delivery, else the run's own session key. That is what stitches a plan
on one node to the work it was handed to on another without either of them
learning about the other, and what lets the pipe's chain and this board point
at each other.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from hookprobe.runs import FAILED, RUNNING, Run

# The states a work item can be in. `received` is not among them on purpose:
# the PRD's state list has one, but in this loop a run is executing the moment
# it exists — there is no queue to sit in, and a state nothing is ever observed
# in is a lie told once per reader.
EXECUTING = "executing"
WAITING_APPROVAL = "waiting_approval"
VERIFYING = "verifying"
NEEDS_HUMAN = "needs_human"
DONE = "done"

# Board order: what a person should look at first. Blocked before busy, because
# a running item needs nothing from anybody and a blocked one is waiting on the
# person reading the board.
BOARD_ORDER = (WAITING_APPROVAL, NEEDS_HUMAN, EXECUTING, VERIFYING, DONE)
BLOCKED = (WAITING_APPROVAL, NEEDS_HUMAN)


@dataclass
class WorkItem:
    """One piece of work, and everything the loop knows about its progress."""

    work_id: str
    title: str = ""
    state: str = DONE
    kind: str = ""
    source: str = ""
    event_id: Any = None
    opened_at: float = 0.0
    updated_at: float = 0.0
    cost_usd: float = 0.0
    turns: int = 0
    sessions: list[str] = field(default_factory=list)
    # What waits on a person right now: {kind, ref, text, session}. `approve`
    # blocks the work; `memory` and `ruling` do not — see `state` below.
    open: list[dict[str, Any]] = field(default_factory=list)
    # What the work produced: {kind, ref, name}. Reports, runbooks, procedures.
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    # Whether anybody says it worked, and what said so.
    # Whether anybody or anything says this ended well, and what said so:
    # `ruling` (a person), `remediation` (its own procedure ran clean),
    # `recovery` (the condition cleared), in that order of strength.
    verified: bool = False
    verified_by: str = ""
    # Whether the work needed a person to proceed — an approval to press, or a
    # question to answer mid-flight. NOT "a person started it": a request is not
    # an intervention, and the metric this feeds asks about intervention.
    hands_on: bool = False
    # Provenance, for the console's one-line answer to "where did this come from".
    asked_by: str = ""
    thread_root: str = ""
    refires: int = 0
    follow_ups: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "work_id": self.work_id,
            "title": self.title,
            "state": self.state,
            "kind": self.kind,
            "source": self.source,
            "event_id": self.event_id,
            "opened_at": self.opened_at,
            "updated_at": self.updated_at,
            "cost_usd": round(self.cost_usd, 6),
            "turns": self.turns,
            "sessions": self.sessions,
            "open": self.open,
            "artifacts": self.artifacts,
            "verified": self.verified,
            "verified_by": self.verified_by,
            "hands_on": self.hands_on,
            "asked_by": self.asked_by,
            "thread_root": self.thread_root,
            "refires": self.refires,
            "follow_ups": self.follow_ups,
        }


def identify(run: Run) -> str:
    """Which work this run belongs to. The door decides; this is the reader."""
    return str(run.meta.get("work_id") or "") or run.session_key


def _artifacts(run: Run) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if run.text and run.status != RUNNING:
        out.append({"kind": "report", "ref": run.session_key, "name": "investigation report"})
    installed = str((run.distilled or {}).get("installed") or "")
    if installed:
        out.append({"kind": "runbook", "ref": installed, "name": installed})
    return out


def _applied_cleanly(row: dict[str, Any]) -> bool:
    """A procedure that ran and whose every step came back 0.

    The one verification this loop can make without a person: the commands the
    report proposed were approved, ran, and none of them failed. A partial run
    (`interrupted`) is not a pass — that is the case this check exists to keep
    out of the completed column.
    """
    if str(row.get("status") or "") != "applied" or row.get("interrupted"):
        return False
    results = row.get("results") or []
    steps = row.get("steps") or []
    return bool(results) and len(results) == len(steps) and all(r.get("exit") == 0 for r in results)


def resolve(
    runs: list[Run],
    *,
    proposals: list[dict[str, Any]] | None = None,
    suggestions: list[dict[str, Any]] | None = None,
    now: float | None = None,
) -> list[WorkItem]:
    """Group runs into work items and settle each one's state.

    The precedence below is the whole state machine, and the order is the
    argument: what is running needs nothing from anybody, what is blocked does,
    and a failure outranks a stale success because the last thing that happened
    is the thing a person has to answer for.
    """
    now = now or time.time()
    by_session_proposals: dict[str, list[dict[str, Any]]] = {}
    for row in proposals or []:
        by_session_proposals.setdefault(str(row.get("session_key") or ""), []).append(row)
    by_session_suggestions: dict[str, list[dict[str, Any]]] = {}
    for row in suggestions or []:
        by_session_suggestions.setdefault(str(row.get("session_key") or ""), []).append(row)

    items: dict[str, WorkItem] = {}
    # Folded in the order the runs happened, so `title` and `opened_at` come
    # from the run that STARTED the work rather than from whichever continuation
    # sorted first. What the board hands back is newest first, like every other
    # list in this stack — see the sort at the end.
    for run in sorted(runs, key=lambda r: r.created_at):
        work_id = identify(run)
        item = items.get(work_id)
        if item is None:
            item = WorkItem(work_id=work_id, opened_at=run.created_at)
            items[work_id] = item
        meta = run.meta or {}
        item.sessions.append(run.session_key)
        item.title = item.title or str(meta.get("title") or "")[:160]
        item.kind = item.kind or str(meta.get("kind") or "")
        item.source = item.source or str(meta.get("source") or "")
        if item.event_id is None:
            item.event_id = meta.get("event_id")
        item.asked_by = item.asked_by or str(meta.get("asked_by") or "")
        item.thread_root = item.thread_root or str(meta.get("thread_root") or "")
        item.refires += int(meta.get("refires") or 0)
        item.follow_ups += len(meta.get("follow_ups") or [])
        item.turns += len(run.turns)
        item.updated_at = max(item.updated_at, run.finished_at or run.created_at)
        costs = [c for c in (t.get("cost_usd") for t in run.turns) if c is not None]
        item.cost_usd += sum(costs) if costs else (run.cost_usd or 0.0)
        item.artifacts.extend(_artifacts(run))

        if run.ruling == "useful":
            item.verified, item.verified_by = True, "ruling"
        if not run.ruling and run.finished:
            item.open.append(
                {"kind": "ruling", "ref": run.session_key, "text": "was this worth it?", "session": run.session_key}
            )
        if meta.get("follow_ups"):
            item.hands_on = True

        for row in by_session_proposals.get(run.session_key, []):
            status = str(row.get("status") or "")
            steps = row.get("steps") or []
            item.artifacts.append(
                {"kind": "procedure", "ref": str(row.get("id") or ""), "name": f"{len(steps)} step(s) · {status}"}
            )
            # A proposal at all means a person had to decide, whichever way they
            # decided — that is what `hands_on` measures.
            item.hands_on = True
            if status == "proposed":
                first = steps[0] if steps else {}
                item.open.append(
                    {
                        "kind": "approve",
                        "ref": str(row.get("id") or ""),
                        "text": str(first.get("command") or first.get("action") or "run the proposed steps")[:120],
                        "session": run.session_key,
                    }
                )
            if _applied_cleanly(row) and not item.verified:
                item.verified, item.verified_by = True, "remediation"
            item.updated_at = max(item.updated_at, float(row.get("created_at") or 0.0))

        # The weakest of the three, and the only one that needs nobody: the
        # condition this work was about has ended. It does not say the
        # investigation was right or that the agent caused the ending — a
        # flapping alert clears on its own — so a ruling or a clean procedure
        # outranks it and this only fills the gap they leave.
        if run.meta.get("recovered_at") and not item.verified:
            item.verified, item.verified_by = True, "recovery"

        for row in by_session_suggestions.get(run.session_key, []):
            item.open.append(
                {
                    "kind": "memory",
                    "ref": str(row.get("id") or ""),
                    "text": str(row.get("line") or "")[:120],
                    "session": run.session_key,
                }
            )

    for item in items.values():
        item.state = _state(item, [r for r in runs if identify(r) == item.work_id])
    return sorted(items.values(), key=lambda i: i.updated_at or i.opened_at, reverse=True)


def _state(item: WorkItem, runs: list[Run]) -> str:
    """The precedence, in one place, with the reason for each step.

    A memory suggestion does NOT block: the report is delivered and the work is
    finished; the line is an offer for the next run, not a gate on this one. It
    still shows on the approvals page, which answers the wider question "what
    waits on a person" — a superset of "what is blocked", on purpose.
    """
    if any(r.status == RUNNING for r in runs):
        return EXECUTING
    if any(o["kind"] == "approve" for o in item.open):
        return WAITING_APPROVAL  # nothing runs until somebody presses
    last = max(runs, key=lambda r: r.finished_at or r.created_at, default=None)
    if last is not None and last.status == FAILED:
        return NEEDS_HUMAN
    if item.verified_by == "remediation" and any(o["kind"] == "ruling" for o in item.open):
        # A procedure ran and every step came back 0, but nobody has said the
        # condition actually cleared. On an unattended deployment items can sit
        # here, and that is the true reading: the loop closed its own half.
        return VERIFYING
    return DONE


def counts(items: list[WorkItem]) -> dict[str, int]:
    """The board's header line, and the numbers the PRD's overview asks for."""
    out = {state: 0 for state in BOARD_ORDER}
    for item in items:
        out[item.state] = out.get(item.state, 0) + 1
    out["blocked"] = sum(out.get(state, 0) for state in BLOCKED)
    # What a person is actually asked to do. Rulings are counted apart on
    # purpose: an approval blocks work and a memory line waits to be written,
    # but a ruling is optional feedback — and on an unattended deployment there
    # are hundreds of them. Folded together, three procedures that block
    # something were invisible behind 164 reports nobody was ever going to rule.
    out["open_items"] = sum(1 for i in items for o in i.open if o["kind"] != "ruling")
    out["unruled"] = sum(1 for i in items for o in i.open if o["kind"] == "ruling")
    out["verified"] = sum(1 for i in items if i.verified)
    # The north star, as far as this node can honestly compute it: finished,
    # verified by a ruling or by its own procedure, and it never had to stop and
    # ask. Zero is a real answer — it says the verification loop is not closed.
    out["closed_unattended"] = sum(1 for i in items if i.state == DONE and i.verified and not i.hands_on)
    return out
