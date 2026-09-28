"""Orchestration: accept a trigger, run the engine once, remember the outcome.

The failure shape is a deliberate choice: a run that dies (exception,
timeout, empty output) still completes the contract — isFinal true, with a
well-formed report whose root_cause says the runner failed. The caller sees
the error within one poll instead of waiting out its own timeout window.

What this module keeps is the part that has to be in one place: the task set, so
a shutdown knows what is in flight; the semaphore, so a storm queues instead of
stampeding; and the guarantee that every run reaches a final state and reports
itself. Everything a run leads to afterwards lives with the thing it is about —
hookprobe.reports writes the report-shaped refusals, hookprobe.notify carries a
relay-born report back to the pipe, hookprobe.remediation runs an approved
procedure, hookprobe.distill_loop decides what the run leaves for the next one.
Those are four different failure modes, and none of them is allowed to cost a
finished report.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import re
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import replace
from typing import Any, Protocol

from hookprobe import (
    actions,
    automation,
    blockers,
    distill,
    distill_loop,
    patches,
    remediation,
    rulings,
    run_rulings,
    suggestions,
)
from hookprobe.distill import CASES_MARKER, slug
from hookprobe.engine import EngineResult, price_tokens, transient, unreachable
from hookprobe.notify import ReturnDelivery
from hookprobe.reports import (
    budget_report,
    cooling_report,
    failure_report,
    outcome_report,
    superseded_report,
    unanswered_report,
)
from hookprobe.runs import COMPLETED, FAILED, INFERRED_BY_PREFIX, RUNNING, Run, RunStore
from hookprobe.settings import Settings

logger = logging.getLogger("hookprobe.service")


def _inferred_actor(session_key: str) -> str:
    """The `ruled_by` for a verdict a patrol inferred, prefixed exactly once."""
    if session_key.startswith(INFERRED_BY_PREFIX):
        return session_key
    return f"{INFERRED_BY_PREFIX}{session_key}"


# One automatic retry per turn, and five seconds before it. A provider blip is
# over in seconds; a second failure means it was not a blip, and a third attempt
# is a bill rather than a diagnosis.
_MAX_AUTO_RETRIES = 1
_RETRY_BACKOFF_SECONDS = 5.0

# What a retried turn is told when the runtime kept a session. Without one the
# original question is simply asked again.
_RETRY_MESSAGE = (
    "The previous attempt was cut off by a provider error, not by anything you did. "
    "Everything you had gathered is still in this session. Continue from where you stopped "
    "and produce the report — do not start the investigation over."
)

# How far back a recovery may reach for the investigation it verifies. A day,
# fixed rather than configurable: a condition that ends within a day of being
# investigated is plainly the same episode, and one that ends three days later
# is verifying an investigation nobody is still reading. If a deployment ever
# needs a different number, it needs it for a reason worth writing down here.
_RECOVERY_WINDOW_SECONDS = 24 * 3600

# How many re-fires one real investigation may answer before the next re-fire
# buys a real look regardless of the window. Fixed rather than configurable for
# the same reason as the recovery window: an alert flapping every five minutes
# with no recovery between would otherwise be answered from one report for the
# whole window, and ten is enough to bend the SES-shaped curve (six re-fires a
# day) without being a number anyone tunes.
_REFIRE_ANSWER_MAX = 10
# How much of the anchoring report a re-fire answer carries. The runbook slot
# is 2500; the finding is the thing being reused, so it gets a little more.
_STANDING_FINDING_MAX = 3000

# One continuation per run, ever. The counter is persisted on the run, so a
# process that crashes on every boot settles the second time instead of buying
# a turn on each restart.
_MAX_RESUMES = 1

# What a continued run is told. It says what happened and forbids the expensive
# mistake: a model that starts over pays for the whole investigation again and
# reports as if the first attempt never happened.
_RESUME_MESSAGE = (
    "The service running this investigation restarted before you finished. "
    "Everything you had gathered is still in this session. Continue from where you stopped "
    "and produce the report — do not start the investigation over."
)


class Engine(Protocol):
    """The runtime contract: what this service needs from whatever runs a turn.

    THREE runtimes exist — `hookprobe/runtimes.py` is the registry and the list
    is `claude`, `codex`, `pi` — and one rehearsal, `replay`, in the same
    registry so the same suite judges it: a recorded investigation played back
    through the same gate and recorder, with no model behind it
    (engine_replay.py). This docstring was written when there was one adapter,
    against the question "what does a second have to fill", and each of the two
    runtimes that followed corrected it on a point its author could not have known:
    codex, that a gate which cannot LAUNCH is not a gate, and pi, that a
    runtime reporting a price is more dangerous than one reporting nothing.
    Both corrections are below, in the obligations they belong to, because a
    contract that only records what its first implementation happened to do is a
    description of that implementation.

    Two of the five obligations are invisible in the signatures below, and
    losing either would take this service's claims with it:

    * **A tool gate that runs BEFORE a tool does.** The read-only posture is not
      a prompt, and — since codex — it is not a mechanism either. It is one
      DECISION, in `hookprobe/gate.py`: which call is refused, why, and what
      gets recorded. An adapter's job is to reach that decision before the shell
      runs, by whatever route its runtime offers (in-process hooks for `claude`,
      a spawned `python -m hookprobe.gate` for `codex`, a shipped extension
      shelling out to the same command for `pi`). Writing the policy twice
      instead is the failure this arrangement exists to prevent: `readonly`
      would mean one thing per engine and nothing in either suite would notice
      the day they diverged. A runtime that can only be *asked* nicely not to
      write cannot be run under `readonly` at all.

      **And an adapter that reaches the gate by spawning must prove the spawn
      works before its first turn.** Codex's first live run had no gate — the
      hook command could not import hookprobe, codex logged that and carried
      on, and `kubectl delete pod` ran to completion on a node whose
      `/v1/agent` said `bash_guard: readonly`, with an empty audit file. Every
      layer behaved reasonably and the boundary was simply absent. So the
      spawning adapters carry `verify_gate()`: hand the gate a call no posture
      permits and refuse to start unless it is refused. It is deliberately NOT
      in this Protocol, because the in-process adapter has no spawn that can
      fail and a signature it cannot meaningfully implement would be ceremony;
      what the contract requires is the PROOF, not the method.
    * **A per-call audit record the agent cannot edit.** `/v1/runs/{key}/audit`
      and the flight recorder under `{workdir}/audit`, written from whatever
      mechanism reaches the gate — including inside subagents, whose calls never
      appear in the message stream. Without them a run's account of itself is
      the run's own word.

    And three that are visible, but easy to satisfy shallowly:

    * **Session identity that outlives this process.** `resume` is handed back
      the id from a previous turn — possibly from a previous *boot*, since
      `recover_orphans` continues a run a crash interrupted. An id that is only
      valid in-process satisfies the type and breaks the feature.
    * **Incremental events**, `on_event`, including `{"type": "session", "id":
      …}` as soon as the id is known. Emitting it only at the end is what made
      an interrupted first turn unrecoverable.
    * **Cost and usage on the result**, or `None` — never zero as a stand-in for
      unknown. The ledger keeps "nobody counted" and "this was free" apart, and
      a runtime that reports 0.0 for an unpriced turn corrupts both the budget
      breaker and the weekly account.

      **A zero with tokens behind it is unpriced, not free** — pi's correction,
      and the sharper rule, because the original wording is satisfied by a
      runtime that sincerely believes 0.0. pi prices a turn from its own model
      catalogue, this deployment's model is not in it, and the first real turn
      returned `totalTokens: 6397` with `cost.total: 0`. A budget breaker fed
      that would watch an unattended node spend all week and see nothing wrong,
      which is worse than a runtime admitting it cannot count. Only a zero with
      nothing spent behind it may mean free; assume a reported price of zero is
      unpriced until you have seen it be right.
    """

    async def run(
        self,
        *,
        message: str,
        session_key: str,
        resume: str | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> EngineResult: ...

    async def stop(self) -> bool:
        """Ask the running turn to wind down; False if there was nothing to ask.

        Optional in practice — the fallback below cancels — but part of the
        contract because the difference is a recorded cost versus None on every
        stop and every restart, not just on a rare timeout.
        """
        ...

    def describe_inputs(self, *, resume: str | None = None) -> dict[str, Any]:
        """The prompt inputs this engine would resolve for the next turn."""
        ...


class RunBusyError(RuntimeError):
    """The session already has a turn in flight."""


class NoTurnRunningError(RuntimeError):
    """Stop was asked for, but nothing is in flight."""


class NotResumableError(ValueError):
    """The run left no engine session behind to resume."""


# What a platform-authored prompt knows about its alert. The agent door takes a
# finished prompt, not an event, so runs born there had no meta at all: the
# board showed thirty rows of the same instruction boilerplate, and nothing —
# not the ruling gate, not the case-file recall — could say which CONDITION a
# run was about. The prompts themselves embed the alert as JSON, so read it
# back out. First match that is not a template placeholder wins: the same
# prompts also embed an OUTPUT template whose every value is "unknown".
_PROMPT_ALERT = {
    "title": re.compile(r'"(?:rule_name|alertname|alert_name)"\s*:\s*"([^"]{1,200})"'),
    # Charset-constrained on purpose: the same prompts also DESCRIBE these
    # fields in prose ("critical | high | medium | ..."), and prose has spaces.
    "source": re.compile(r'"source"\s*:\s*"([A-Za-z0-9_.-]{1,40})"'),
    "level": re.compile(r'"(?:level|severity)"\s*:\s*"([A-Za-z]{1,20})"'),
}
_TEMPLATE_VALUES = ("", "unknown", "null")


def alert_meta_from_prompt(message: str) -> dict[str, str]:
    """{"title": ..., "source": ..., "level": ...} — whatever the prompt states."""
    head = message[:30000]
    out: dict[str, str] = {}
    for key, pattern in _PROMPT_ALERT.items():
        for match in pattern.finditer(head):
            value = match.group(1).strip()
            if value.lower() not in _TEMPLATE_VALUES:
                out[key] = value
                break
    return out


class RunService:
    def __init__(self, settings: Settings, engine: Engine, store: RunStore) -> None:
        self._settings = settings
        self._engine = engine
        self._store = store
        self._semaphore = asyncio.Semaphore(settings.max_concurrent)
        self._tasks: set[asyncio.Task[None]] = set()
        self._running: dict[str, asyncio.Task[None]] = {}
        self._stop_requested: set[str] = set()
        self._in_slot = 0
        # Live watchers of a session's process feed, one queue each. A run
        # already publishes its steps through on_event; this is the seam that
        # lets a browser see them as they happen instead of on the next poll.
        self._watchers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = {}
        # Set by the app: "the session list moved" — a run started, settled, or
        # returned its report. Separate from the per-run feed above, because a
        # list and a transcript answer different questions.
        self.on_board_change: Callable[[], None] | None = None
        # The family loop's last mile, and the alarm behind it.
        self._returns = ReturnDelivery(settings, store)
        # Return-retry pacing, an instance attr so tests can collapse it.
        self._return_delays: tuple[float, ...] = (0.0, 2.0, 5.0)
        # The same, for the pause before one more attempt at a provider blip.
        # Not a setting: five seconds is not a number anyone tunes, and a test
        # that had to wait it out would be five seconds slower for nothing.
        self.retry_backoff_seconds: float = _RETRY_BACKOFF_SECONDS

    def start(self, payload: dict[str, Any], *, origin: str = "") -> Run:
        """Idempotent per sessionKey: re-triggering an existing run returns it."""
        session_key = str(payload.get("sessionKey") or "") or f"hookprobe:{uuid.uuid4()}"
        existing = self._store.get(session_key)
        if existing is not None:
            return existing

        message = str(payload.get("message") or "")
        if not message.strip():
            raise ValueError("message must not be empty")

        timeout_s = self._clamp_timeout(payload.get("timeoutSeconds"))
        run = Run(
            session_key=session_key,
            run_id=uuid.uuid4().hex[:12],
            current_message=message,
            model=self._settings.model,
            model_endpoint=self._settings.model_endpoint,
            origin=origin,
        )
        run.meta = dict(payload.get("_meta") or {})
        if session_key.startswith(tuple(self._settings.synthetic_key_prefixes)):
            # A drill, a by-hand check, a wiring test: real machinery on unreal
            # work. Marked once here so every reader — the distiller, the re-fire
            # anchor, the weekly page, the board — can leave it out.
            run.meta["synthetic"] = True
        if not run.meta.get("title"):
            derived = alert_meta_from_prompt(message)
            if derived.get("title"):
                for key, value in derived.items():
                    run.meta.setdefault(key, value)
                # Marked, because a derived fact and a stated one must stay
                # distinguishable when one of them turns out wrong.
                run.meta["meta_derived"] = "prompt"
        self._store.create(run)
        self._board_changed()
        answer = (
            None
            if payload.get("force")
            else (self._runbook_answer(run) or self._runbook_answer_verified(run) or self._refire_answer(run))
        )
        if answer is not None:
            self._finish_without_engine(run, answer)
            return run
        self._spawn(run, message, timeout_s, resume=None)
        return run

    def _runbook_answer(self, run: Run) -> str | None:
        """The report for a condition a standing ruling says is not worth a run.

        Every clause here is a reason to investigate ANYWAY, and the order is
        cheapest-first. The gate never applies to patrols or consolidations
        (they are about the loop, not an alert), needs a fresh not_worth_it
        ruling (the weekly patrol refiles what it can still defend), needs the
        consolidated runbook it would answer from — and still lets a REAL run
        through every ruling_reverify_days, because a ruling whose evidence
        nobody re-checks is a prejudice with a timestamp.
        """
        title = str(run.meta.get("title") or "").strip()
        if not title or run.meta.get("patrol") or run.meta.get("consolidates"):
            return None
        ruling = rulings.standing(self._settings.workdir, title, ttl_days=self._settings.ruling_ttl_days)
        if ruling is None or ruling.get("verdict") != "not_worth_it":
            return None
        manifest = self._settings.workdir / ".claude" / "skills" / slug(title) / "SKILL.md"
        try:
            procedure = manifest.read_text(encoding="utf-8").split(CASES_MARKER, 1)[0].strip()
        except OSError:
            return None
        cutoff = time.time() - self._settings.ruling_reverify_days * 86400
        recently_verified = any(
            str(other.meta.get("title") or "") == title
            and not other.meta.get("answered_from_runbook")
            and (other.finished_at or 0) >= cutoff
            for other in self._store.list_runs(limit=200)
            if other.session_key != run.session_key
        )
        if not recently_verified:
            return None
        # Report-shaped JSON, the same dialect as failure_report: report_summary
        # lifts `summary` for the card, and a machine caller polling /final gets
        # a parseable object instead of prose it did not ask for.
        age_days = int((time.time() - float(ruling.get("at") or 0)) / 86400)
        return json.dumps(
            {
                "summary": (
                    f"已按 runbook 直接作答，未启动引擎（$0）。该条件 {age_days} 天前被裁定 not_worth_it："
                    f"{ruling.get('why', '')}"
                ),
                # The platform that polls /final reads root_cause for display
                # (verified in its consumer: any balanced JSON dict is adopted
                # whole, raw text retained). Same sentence as summary, in the
                # slot the caller's UI actually shows.
                "root_cause": f"条件已被裁定 not_worth_it（{age_days} 天前）：{ruling.get('why', '')}",
                "verdict": "not_worth_it",
                "ruled_at_days_ago": age_days,
                "runbook": procedure[:2500],
                "answered_from_runbook": True,
                "how_to_reinvestigate": 'POST /hooks/agent with {"force": true}; full runs also recur',
            },
            ensure_ascii=False,
            indent=2,
        )

    def _runbook_answer_verified(self, run: Run) -> str | None:
        """Answer a re-fire of a WORTH-IT condition from the runbook a person
        vouched for, instead of paying for a cold-start.

        The mirror of _runbook_answer, and the safer half is the gate: not a
        "not worth it" ruling but a USEFUL one on the condition's most recent
        REAL investigation. A person pressing useful is a person saying the
        method in that runbook works, which is exactly the licence to reuse it;
        a useless press withdraws the runbook (its SKILL.md is removed), so a
        condition that stopped being understood stops being answered this way.

        Never a silence. This returns a report that DELIVERS like any other —
        marked answered-from-runbook and $0, carrying the procedure and how to
        force a real run — so the operator still sees the re-fire and loses only
        the automatic re-investigation, reversibly. And never off a run that was
        itself answered from a runbook: only a REAL useful run vouches, or a
        chain of runbook-answers would keep citing itself.
        """
        days = self._settings.runbook_answer_days
        title = str(run.meta.get("title") or "").strip()
        if days <= 0 or not title or run.meta.get("patrol") or run.meta.get("consolidates"):
            return None
        manifest = self._settings.workdir / ".claude" / "skills" / slug(title) / "SKILL.md"
        try:
            procedure = manifest.read_text(encoding="utf-8").split(CASES_MARKER, 1)[0].strip()
        except OSError:
            return None  # no runbook, or it was withdrawn — a cold start reverifies
        vouched = self._vouching_run(title, time.time() - days * 86400, exclude=run.session_key)
        if vouched is None:
            return None  # nobody has vouched recently; a real run earns the licence
        age_days = int((time.time() - float(vouched.ruled_at or 0)) / 86400)
        return json.dumps(
            {
                "summary": (
                    f"已按 runbook 直接作答，未启动引擎（$0）。该条件最近一次调查在 {age_days} 天前被裁定 "
                    f"useful，方法见下；看着不对就用 force 重跑一次真查。"
                ),
                "root_cause": f"已知条件，按 {age_days} 天前裁定 useful 的 runbook 作答（$0，未重新调查）。",
                "verdict": "known_condition",
                "vouched_days_ago": age_days,
                "runbook": procedure[:2500],
                "answered_from_runbook": True,
                "how_to_reinvestigate": (
                    'POST /hooks/agent with {"force": true}; a real run also recurs once the window lapses'
                ),
            },
            ensure_ascii=False,
            indent=2,
        )

    def _refire_answer(self, run: Run) -> str | None:
        """Answer a re-fire of a condition this node investigated FOR REAL a few
        hours ago from that investigation's report, instead of paying to derive
        it again.

        The third $0 path, and the one that waits for no ruling. The two above
        wait for a verdict — a standing not_worth_it, or a useful press on the
        last real run — and on the deployment this was measured on the verdict
        arrives days after the money is spent: one SES condition re-fired every
        four hours through 2026-09-07/08, six cold starts at $0.65–2.71 reached
        one finding and five proposed the same read-only check, and the patrol's
        `useful` landed on the Wednesday. The coalesce window is minutes and
        never sees a four-hour cadence; this does.

        Every clause is a reason to investigate ANYWAY, cheapest-first:

        * off unless `refire_answer_hours` is set — answering from a report is a
          claim about the world, and the default makes none;
        * never for a patrol, a consolidation, a task or a brief, or anything a
          person asked for in chat — a person asking deserves a run;
        * the newest REAL run of the same (source, title) must be a clean,
          completed one inside the window, and it is the anchor. A runbook answer
          never anchors — only a run that looked vouches, or a chain of answers
          would cite itself past the window and forever. A newer real run that
          FAILED forfeits the anchor: the last look did not finish, so this one
          must;
        * the level must not have moved — a `high` that comes back `critical` is
          a different question;
        * no recovery may be recorded on the condition since the anchor started.
          An alert that ended and fired again is a new episode. `record_recovery`
          annotates the NEWEST run of the condition, which may be one of these
          answers, so every run since the anchor is checked and not the anchor
          alone;
        * at most _REFIRE_ANSWER_MAX answers per anchor, so an alert flapping
          every five minutes with no recovery still buys a real look.

        Never a silence: the reply is a report that DELIVERS like any other,
        marked answered-from-runbook and $0, carrying the standing finding, the
        runbook when one exists, and how to force a real run. `meta.refire_of`
        names the anchor so the board and the report agree on what answered.
        """
        hours = self._settings.refire_answer_hours
        meta = run.meta or {}
        title = str(meta.get("title") or "").strip()
        if hours <= 0 or not title or meta.get("patrol") or meta.get("consolidates"):
            return None
        if str(meta.get("kind") or "alert") != "alert" or meta.get("asked_by") or meta.get("thread_root"):
            return None
        source = str(meta.get("source") or "")
        level = str(meta.get("level") or "").strip().lower()
        now = time.time()
        condition = [  # newest first, like the store
            other
            for other in self._store.list_runs(limit=200)
            if other.session_key != run.session_key
            and str((other.meta or {}).get("title") or "") == title
            and str((other.meta or {}).get("source") or "") == source
            and not (other.meta or {}).get("notice")
            and not (other.meta or {}).get("patrol")
            and not (other.meta or {}).get("consolidates")
            and not (other.meta or {}).get("synthetic")
        ]
        anchor = next((other for other in condition if not (other.meta or {}).get("answered_from_runbook")), None)
        if anchor is None or anchor.status != COMPLETED or anchor.error or not anchor.text:
            return None
        anchored_at = anchor.finished_at or anchor.created_at
        if anchored_at < now - hours * 3600:
            return None
        if str((anchor.meta or {}).get("level") or "").strip().lower() != level:
            return None
        since_anchor = [other for other in condition if other.created_at >= anchor.created_at]
        if any((other.meta or {}).get("recovered_at") for other in since_anchor):
            return None
        answers = sum(1 for other in since_anchor if (other.meta or {}).get("answered_from_runbook"))
        if answers >= _REFIRE_ANSWER_MAX:
            return None
        manifest = self._settings.workdir / ".claude" / "skills" / slug(title) / "SKILL.md"
        try:
            procedure = manifest.read_text(encoding="utf-8").split(CASES_MARKER, 1)[0].strip()
        except OSError:
            procedure = ""  # a report is enough to answer from; the runbook is a bonus
        age_hours = (now - anchored_at) / 3600
        run.meta["refire_of"] = anchor.session_key
        return json.dumps(
            {
                "summary": (
                    f"已按 {age_hours:.1f} 小时前对同一条件的真实调查直接作答，未启动引擎（$0）。"
                    f"级别未变（{level or '未标'}），期间无恢复记录；这是那次调查之后的第 {answers + 1} 次重发。"
                    "看着不一样就用 force 重跑一次真查。"
                ),
                "root_cause": (
                    f"同一条件 {age_hours:.1f} 小时前已真实调查（session {anchor.session_key}），"
                    "本次未重新验证；当时的结论见 standing_finding。"
                ),
                "verdict": "recurring_condition",
                "refire_of": anchor.session_key,
                "anchor_hours_ago": round(age_hours, 1),
                "refires_since_anchor": answers + 1,
                "standing_finding": anchor.text[:_STANDING_FINDING_MAX],
                "runbook": procedure[:2500],
                "answered_from_runbook": True,
                "how_to_reinvestigate": (
                    'POST /hooks/agent with {"force": true}; a real run also recurs once the window lapses, '
                    f"the level changes, a recovery arrives, or after {_REFIRE_ANSWER_MAX} answers"
                ),
            },
            ensure_ascii=False,
            indent=2,
        )

    def _vouching_run(self, title: str, cutoff: float, *, exclude: str = "") -> Run | None:
        """The most recent REAL investigation of this condition a person called
        useful, inside the window — or None.

        One home for the predicate, because two readers ask it: the answer path
        below, and the readiness figure `/v1/stats` reports. A rule written
        twice is a rule that will eventually say two things, which is how
        `work.py` came to read a procedure status nothing ever wrote.

        `answered_from_runbook` runs are excluded on purpose: only a real run
        vouches, or a chain of runbook answers would keep citing itself.
        """
        return max(
            (
                other
                for other in self._store.list_runs(limit=200)
                if str(other.meta.get("title") or "") == title
                and other.ruling == "useful"
                and not other.meta.get("answered_from_runbook")
                and not other.meta.get("synthetic")
                and other.session_key != exclude
                and (other.ruled_at or 0) >= cutoff
            ),
            key=lambda r: r.ruled_at or 0.0,
            default=None,
        )

    def runbook_readiness(self) -> dict[str, Any]:
        """Whether the $0 answer path CAN fire, and when it cannot, why.

        This deployment carries 21 runbooks — six of them for the same SES
        conditions it re-investigates from cold every time — and the path that
        would answer from them has never fired. Nothing anywhere said why, and
        the reason takes three modules to reconstruct: the knob is off AND no
        report has ever been ruled, and the ruling is the licence. A feature
        that is configured and inert is indistinguishable from one that is
        working, unless it says so.

        `blocked_by` names ONE reason, the first that applies, because a list of
        four blockers is a list nobody acts on. It is prose rather than a code
        so it can be read straight off the page and acted on.
        """
        skills = self._settings.workdir / ".claude" / "skills"
        library = len(list(skills.glob("*/SKILL.md"))) if skills.is_dir() else 0
        days = self._settings.runbook_answer_days
        ready: list[str] = []
        if days > 0 and library:
            cutoff = time.time() - days * 86400
            seen = {str(r.meta.get("title") or "") for r in self._store.list_runs(limit=200)}
            ready = [
                title
                for title in sorted(t for t in seen if t)
                if (skills / slug(title) / "SKILL.md").is_file() and self._vouching_run(title, cutoff) is not None
            ]
        if days <= 0:
            blocked = "HOOKPROBE_RUNBOOK_ANSWER_DAYS is 0, so the $0 answer path is switched off"
        elif not library:
            blocked = "no runbooks: nothing has been distilled to answer from"
        elif not ready:
            blocked = (
                f"no condition has a `useful` ruling inside {days}d — a person vouching for a runbook "
                "is what licenses reusing it, and an unruled report vouches for nothing"
            )
        else:
            blocked = ""
        return {
            "library": library,
            "answer_days": days or None,
            "conditions_ready": len(ready),
            "blocked_by": blocked or None,
        }

    def _finish_without_engine(self, run: Run, text: str) -> None:
        """Complete a run the gate answered: same ledger, same return leg, no
        engine and no bill. The distill column says why nothing was learned."""
        run.text = text
        run.status = COMPLETED
        run.finished_at = time.time()
        run.cost_usd = 0.0
        run.meta["answered_from_runbook"] = True
        run.distilled = {"skipped": "answered from the runbook, nothing new to learn"}
        self._record_turn(run, None)
        self._settle(run)
        self._schedule_return(run)
        logger.info("run answered from runbook session=%s title=%r", run.session_key, run.meta.get("title"))

    def continue_run(self, session_key: str, payload: dict[str, Any]) -> Run:
        """Reopen a finished investigation with a follow-up message.

        The engine session keeps everything the first pass gathered — tool
        output, evidence, dead ends — so the follow-up explores from there
        instead of starting cold. /final then serves the newest answer.
        """
        run = self._store.get(session_key)
        if run is None:
            raise LookupError("session not found")
        if not run.finished:
            raise RunBusyError("a turn is already in progress for this session")
        if not run.engine_session_id:
            raise NotResumableError("this run left no engine session to resume")

        message = str(payload.get("message") or "")
        if not message.strip():
            raise ValueError("message must not be empty")

        timeout_s = self._clamp_timeout(payload.get("timeoutSeconds"))
        run.status = RUNNING
        run.model = run.model or self._settings.model  # backfill for pre-model records
        run.model_endpoint = run.model_endpoint or self._settings.model_endpoint
        run.run_id = uuid.uuid4().hex[:12]
        run.text = ""
        run.error = None
        run.finished_at = None
        # The previous turn's bill is not this turn's. Left in place, a follow-up
        # that died before the engine reported anything recorded the earlier
        # figure again, so a $2 turn plus a failed follow-up billed $4 to
        # window_spend() and to the session total the console shows.
        run.cost_usd = None
        run.current_message = message
        self._spawn(run, message, timeout_s, resume=run.engine_session_id)
        return run

    def retry(self, session_key: str, *, by: str = "operator") -> Run:
        """Try a failed investigation again, because a person said so.

        The counterpart to `recover_orphans`, for the failures nothing automatic
        will pick up: a timeout, a provider error, a restart that had no session
        to continue. Until now the only way back was to re-fire the alert or
        retype the question, which means the operator carries what the service
        already knows.

        Continues the engine session when there is one — everything the failed
        attempt gathered comes with it — and otherwise re-asks the question the
        run opened with. Not budget-gated, for the reason `/hooks/agent` is not:
        a human's explicit request should not bounce off a meter. Not capped
        either; pressing it again is a person's decision, and every press is a
        turn in the record with the name of whoever asked.
        """
        run = self._store.get(session_key)
        if run is None:
            raise LookupError("session not found")
        if not run.finished:
            raise RunBusyError("a turn is already in progress for this session")
        if run.status != FAILED:
            raise ValueError("only a failed run can be retried")
        opening = str((run.turns[0].get("message") if run.turns else "") or run.current_message or "").strip()
        if run.engine_session_id:
            message, resume = _RESUME_MESSAGE, run.engine_session_id
        elif opening:
            message, resume = opening, None
        else:
            raise NotResumableError("this run kept neither an engine session nor its opening question")
        run.meta["retries"] = int(run.meta.get("retries") or 0) + 1
        run.meta["retried_by"] = by[:80]
        run.status = RUNNING
        run.run_id = uuid.uuid4().hex[:12]
        run.text = ""
        run.error = None
        run.finished_at = None
        run.cost_usd = None
        run.return_status = ""
        run.current_message = message
        logger.info("retry session=%s by=%s resume=%s", session_key, by, resume or "-")
        self._spawn(run, message, self._settings.default_timeout_seconds, resume=resume)
        return run

    def stop(self, session_key: str) -> Run:
        """End the in-flight turn; it finishes as a failed turn, not a hang.

        INTERRUPT, not cancel. Cancelling the coroutine discarded the SDK's final
        message and with it the turn's cost, so every Stop an operator pressed
        recorded None — "nobody counted" — for a run the provider had billed in
        full. Asking the SDK to stop lets that message arrive.

        The cancel is still there as a fallback, on a short fuse: a turn that has
        not reached the SDK yet has nothing to interrupt, and an interrupt the SDK
        ignores must not leave a turn running forever. Answering the operator
        immediately matters more than waiting to see which path won, so the
        arrangement runs in the background and this returns now.
        """
        run = self._store.get(session_key)
        if run is None:
            raise LookupError("session not found")
        task = self._running.get(session_key)
        if run.finished or task is None:
            raise NoTurnRunningError("no turn is in flight for this session")
        self._stop_requested.add(session_key)
        closer = asyncio.create_task(self._interrupt_then_cancel(task))
        self._tasks.add(closer)
        closer.add_done_callback(self._tasks.discard)
        return run

    async def _wind_down(self, turn: asyncio.Task[Any], timeout_s: int, grace: float = 15.0) -> Any:
        """The clock ran out: ask the turn to end, and take its result if it can.

        Returning a real EngineResult here is the point. The SDK reports dollars
        only on its final message, so a turn killed outright recorded cost None —
        "nobody counted" — for the longest and therefore most expensive runs
        there are. Interrupting lets that message arrive, and the result carries
        both the bill and the SDK's own terminal_reason.

        Re-raises TimeoutError when the interrupt does not land, which is the old
        behaviour and the honest one: at that point nobody counted, and the
        unpriced_turns figure is what says so.
        """
        stop = getattr(self._engine, "stop", None)
        if stop is not None:
            try:
                if await stop():
                    settled = await asyncio.wait_for(turn, timeout=grace)
                    # It ended on OUR clock, not its own. The SDK may report a
                    # clean finish for an interrupted turn, and letting that read
                    # as success would turn "we cut it off at 900s" into "it
                    # answered" — with a truncated report standing in for one.
                    # The cost is what we came for; the verdict stays a timeout.
                    return replace(
                        settled,
                        error=settled.error or f"timed out after {timeout_s}s (interrupted; cost recorded)",
                    )
            except Exception:  # noqa: BLE001 — any failure here falls through to the kill
                logger.warning("interrupt after timeout did not settle the turn; cancelling")
        turn.cancel()
        with contextlib.suppress(BaseException):
            await turn
        raise TimeoutError

    async def _interrupt_then_cancel(self, task: asyncio.Task[Any], grace: float = 10.0) -> None:
        """Ask the SDK to stop, and cancel only if it does not.

        The grace period is what buys the accounting: winding a turn down means
        the SDK finishes its current step and emits a ResultMessage, which takes
        a moment. Cancelling immediately would be the old behaviour with extra
        steps.
        """
        interrupted = False
        stop = getattr(self._engine, "stop", None)
        if stop is not None:
            try:
                interrupted = bool(await stop())
            except Exception:  # noqa: BLE001 — the fallback below is the point
                logger.exception("engine stop() raised; cancelling instead")
        if interrupted:
            _, pending = await asyncio.wait({task}, timeout=grace)
            if not pending:
                return  # it wound down on its own, with its bill
            logger.warning("interrupt did not settle the turn in %.0fs; cancelling", grace)
        task.cancel()

    def _spawn(self, run: Run, message: str, timeout_s: int, *, resume: str | None) -> None:
        run.events = []
        self._stop_requested.discard(run.session_key)
        # Checkpoint before the task exists: if the process dies mid-flight,
        # the next boot's sweep finds this stub and completes the loop.
        self._store.checkpoint(run)
        task = asyncio.create_task(self._execute(run, message, timeout_s, resume=resume))
        self._tasks.add(task)
        self._running[run.session_key] = task

        def _done(t: asyncio.Task[None], key: str = run.session_key) -> None:
            self._tasks.discard(t)
            if self._running.get(key) is t:
                del self._running[key]

        task.add_done_callback(_done)

    def recover_orphans(self) -> tuple[int, int]:
        """Runs a previous process left mid-flight: continue them, or settle them.

        Live state does not survive a restart, but a relay-born investigation
        has no poller on the other side — only a pipe waiting for probe-notify.
        Silence would break "failure completes the loop", so an orphan that
        cannot be continued still becomes a failed run that reports itself.

        Continuing is the better answer where it is available. The engine's
        transcript lives on the data volume, not in this process, so a session
        id is a handle to everything the interrupted attempt gathered — tool
        output, evidence, dead ends. Failing the run threw all of it away and
        reported a failure an operator then re-asked by hand, paying twice.

        Four things bound it, because this is the one path that spends money
        with nobody asking:

        * a session id must exist — recorded mid-turn now (`on_event`), so a
          first turn cut off after its first message is resumable at all;
        * ONE resume per run, counted in `meta.resumes` and persisted, so a
          crash loop cannot become a spend loop;
        * the budget breaker, checked here as the event door checks it;
        * `HOOKPROBE_RESUME_INTERRUPTED=off`, for a deployment that would
          rather no restart ever spend on its own.

        The interrupted attempt is recorded as a turn of its own with no cost —
        `None`, "nobody counted", which is the truth: the provider billed
        whatever it billed and no result ever came back to say. It shows in
        `window_unpriced()`, where it belongs.

        Returns (resumed, failed).
        """
        resumed = failed = 0
        for run in self._store.list_runs(limit=1000):
            if run.finished or run.session_key in self._running:
                continue
            if self._can_resume(run):
                self._resume_interrupted(run)
                resumed += 1
            else:
                self._fail(run, "interrupted by a restart before the investigation finished")
                failed += 1
        if resumed or failed:
            logger.warning(
                "restart left %s run(s) mid-flight: %s continued, %s settled as failed",
                resumed + failed,
                resumed,
                failed,
            )
        return resumed, failed

    def _can_resume(self, run: Run) -> bool:
        if not self._settings.resume_interrupted or not run.engine_session_id:
            return False
        if int(run.meta.get("resumes") or 0) >= _MAX_RESUMES:
            return False
        state = self.budget_state()
        return not (state is not None and state[0] >= state[1])

    def _resume_interrupted(self, run: Run) -> None:
        """Record the lost attempt, then continue the session it left behind."""
        run.error = "interrupted by a restart"
        run.text = ""
        run.cost_usd = None
        self._record_turn(run, None)
        run.meta["resumes"] = int(run.meta.get("resumes") or 0) + 1
        run.error = None
        run.status = RUNNING
        run.run_id = uuid.uuid4().hex[:12]
        run.finished_at = None
        run.current_message = _RESUME_MESSAGE
        logger.info("resuming interrupted run session=%s engine=%s", run.session_key, run.engine_session_id)
        self._spawn(run, _RESUME_MESSAGE, self._settings.default_timeout_seconds, resume=run.engine_session_id)

    def sweep_interrupted_remediations(self) -> int:
        """Settle procedures a previous process died in the middle of, at startup.

        The twin of the run sweep above, for the worse case. `running` is a
        state only a live task can leave, and both approve and reject require
        `proposed` — so a restart between step 1 and step 3 stranded the row
        there for good: the remaining steps unrun, and no record anywhere that
        half a procedure had been applied to the target. "Steps 1 and 3 ran" is
        the outcome approve_remediation refuses a proposal whole to avoid; this
        is the same accident arriving by way of a dead process, and it must at
        least be written down.
        """
        settled = remediation.settle_interrupted(self._settings.workdir)
        for row in settled:
            interrupted = row.get("interrupted") or {}
            logger.warning(
                "remediation interrupted by a restart id=%s ran=%s not_run=%s",
                row.get("id"),
                len(interrupted.get("ran") or []),
                len(interrupted.get("not_run") or []),
            )
        if settled:
            self._board_changed()
        return len(settled)

    async def shutdown(self, *, grace_seconds: float = 5.0) -> int:
        """Settle background work before the process goes away; returns how many
        tasks had to be cancelled at the deadline.

        Every task here runs detached from the request that started it — a turn,
        a return delivery, an approved procedure — and nothing used to wait for
        any of them. The procedure is why this exists: its steps run
        sequentially, stop-on-first-failure, so a process that exits between
        step 1 and step 3 leaves a half-applied change and a row still saying
        `running`, which no operator action can move.

        A turn in flight is cancelled outright rather than waited for: _execute
        settles it as a failure that reports itself, which beats both the next
        boot's sweep and holding the container's stop timeout open for a
        thirty-minute investigation. The grace period is for the work with no
        such recovery — the procedure mid-sequence, and the deliveries those
        settlements just queued.
        """
        # Interrupt the turns in flight rather than killing them. This path runs
        # on every deploy, so it was the most frequent of the three that threw a
        # turn's bill away — a restart during three investigations lost three
        # costs, every time, and nothing in the ledger said a number was missing
        # rather than zero.
        stop = getattr(self._engine, "stop", None)
        if stop is not None and self._running:
            with contextlib.suppress(Exception):
                await stop()
        for task in tuple(self._running.values()):
            task.cancel()
        deadline = time.monotonic() + max(0.0, grace_seconds)
        while True:
            pending = {task for task in self._tasks if not task.done()}
            if not pending:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            logger.info("shutdown: waiting on %s background task(s)", len(pending))
            await asyncio.wait(pending, timeout=remaining)
        cancelled = 0
        for task in tuple(self._tasks):
            if not task.done():
                task.cancel()
                cancelled += 1
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)
        if cancelled:
            logger.warning("shutdown cancelled %s task(s) unfinished after %.1fs", cancelled, grace_seconds)
        return cancelled

    def budget_state(self) -> tuple[float, float] | None:
        """(spent_in_window, budget) — None when the breaker is disabled."""
        if self._settings.budget_usd <= 0:
            return None
        return self.window_spend(), self._settings.budget_usd

    def window_cache(self) -> tuple[int, int, int]:
        """(fresh, cached, written) input tokens over the budget window."""
        cutoff = time.time() - self._settings.budget_window_hours * 3600
        return self._store.cache_since(cutoff)

    def window_rulings(self) -> tuple[int, int, int, int]:
        """(investigations, useful, useless, of those inferred) over the window.

        The fourth number is not decoration: a patrol can infer verdicts, and a
        worth figure quoted at somebody deciding whether to pay must not be the
        model's own opinion of itself presented as a person's.
        """
        cutoff = time.time() - self._settings.budget_window_hours * 3600
        return self._store.rulings_since(cutoff)

    def record_ruling(
        self, session_key: str, ruling: str, *, actor: str = "", why: str = ""
    ) -> tuple[Run, list[dict[str, Any]]]:
        """Write down whether this investigation was worth its bill.

        The cost of an investigation has always been countable and its worth was
        countable nowhere, which left the adoption question — "you want me to pay
        a model per alert?" — with a dollar figure and no answer. This is the
        other half of that figure. No property of a report says whether it found
        the cause, so the verdict comes from outside it: a person pressing a card
        button, a person clearing a backlog through the bulk door, or a patrol
        inferring it from the run's own evidence — `actor` says which, and a
        `patrol:` prefix is what keeps an inference out of the human count.

        An empty `ruling` CLEARS one, which the bulk door needs and the card path
        never asks for: undoing a mistaken verdict has to be possible, or the
        column becomes append-only and stops being worth trusting.

        Persisted through annotate() rather than finish(), so a ruling on an old
        investigation does not restamp it as having just finished.
        """
        if ruling and ruling not in actions.RULINGS:
            raise ValueError(f"ruling must be one of {', '.join(actions.RULINGS)}, or empty to clear it")
        run = self._store.get(session_key)
        if run is None:
            raise LookupError("session not found")
        run.ruling = ruling
        run.ruled_at = time.time() if ruling else None
        run.ruled_by = actor[:120] if ruling else ""
        run.ruled_why = why[:400] if ruling else ""
        self._store.annotate(run)
        self._board_changed()
        logger.info("ruling recorded session=%s ruling=%s by=%s", run.session_key, ruling or "(cleared)", actor or "-")
        reconsidered = self._reconsider_runbooks(session_key) if ruling == "useless" else []
        return run, reconsidered

    def _reconsider_runbooks(self, session_key: str) -> list[dict[str, Any]]:
        """A useless ruling withdraws or flags the runbooks that run distilled.

        The wire the learning loop was missing: auto_distill refuses to write
        from a run that failed or produced nothing, but "later judged useless"
        arrived after the write and nothing acted on it — so runbooks whose every
        case came from a worthless run were loaded into every later run as method
        to copy. This closes it at the moment the ruling lands.

        A withdrawal is a `distill` regret and is recorded as one: it is the
        after-the-fact signal that an auto-applied runbook was wrong, which is
        exactly what keeps the distill class honestly below auto_apply in the
        graduation record.
        """
        skills_dir = self._settings.workdir / ".claude" / "skills"

        def ruling_of(sk: str) -> str:
            other = self._store.get(sk)
            return (other.ruling or "") if other else ""

        outcomes = distill.reconsider_after_useless(skills_dir, session_key, ruling_of, at=time.time())
        for outcome in outcomes:
            logger.info("runbook %s: %s (%s)", outcome["runbook"], outcome["action"], outcome)
            if outcome["action"] == "withdrawn":
                automation.record(
                    self._settings.workdir,
                    "distill",
                    outcome["runbook"],
                    "regretted",
                    note="every case was ruled useless",
                )
        return outcomes

    def window_unpriced(self) -> int:
        """How many turns in the window spent money nobody could count.

        The figure below is a floor, not an invoice, and this says how far the
        floor might be off: one timed-out investigation is the most expensive
        kind of turn there is and the one the engine never gets to bill.
        """
        cutoff = time.time() - self._settings.budget_window_hours * 3600
        return self._store.unpriced_since(cutoff)

    def window_spend(self) -> float:
        """What the window has cost so far, ceiling or no ceiling.

        Knowing the spend and capping it are different questions: an operator
        wants the first answered even when they have chosen not to ask the
        second.
        """
        cutoff = time.time() - self._settings.budget_window_hours * 3600
        return self._store.spend_since(cutoff, self.pricer())

    def pricer(self) -> Callable[[Any], float | None] | None:
        """This node's price for a set of recorded tokens, or None when it
        declares no rates.

        Handed to every reader of a recorded cost (runs.turn_cost), so the
        budget window, the run list, the run page, the waterfall and the work
        board all price a turn the same way. With rates declared, a turn is
        priced from its TOKENS — measured — rather than from the dollar figure
        stored beside them, which on a node that had no rates was the runtime's
        own table for a model it was not billing (the local work stack, 30 to
        60 times the gateway's rate for weeks). Without rates this is None and
        every reader keeps the recorded figure, which is the pre-existing
        behaviour on any deployment that has not stated a rate.
        """
        rates = self._rates()
        if not any(rate > 0 for rate in rates):
            return None
        return lambda usage: price_tokens(usage, rates)

    def refuse_for_budget(self, payload: dict[str, Any], *, origin: str, spent: float) -> Run:
        """Settle the session as a refused run — no engine, cost 0, loop completed.

        Idempotent like start(): if the session already exists (an earlier,
        funded investigation), that run is returned untouched.
        """
        session_key = str(payload.get("sessionKey") or "") or f"hookprobe:{uuid.uuid4()}"
        existing = self._store.get(session_key)
        if existing is not None:
            return existing
        run = Run(
            session_key=session_key,
            run_id=uuid.uuid4().hex[:12],
            current_message=str(payload.get("message") or ""),
            model=self._settings.model,
            model_endpoint=self._settings.model_endpoint,
            origin=origin,
        )
        run.meta = dict(payload.get("_meta") or {})
        self._store.create(run)
        run.status = FAILED
        run.error = (
            f"refused: budget exhausted (${spent:.2f} of ${self._settings.budget_usd:.2f} "
            f"in the last {self._settings.budget_window_hours:g}h)"
        )
        run.cost_usd = 0.0
        run.text = budget_report(spent, self._settings.budget_usd, self._settings.budget_window_hours)
        self._record_turn(run, None)
        self._store.finish(run)
        self._schedule_return(run)
        logger.warning("run refused session=%s reason=%s", run.session_key, run.error)
        return run

    def watch(self, session_key: str) -> asyncio.Queue[dict[str, Any]]:
        """Register a live watcher of one session's feed.

        Bounded on purpose: a browser that stops reading must not let a running
        investigation grow an unbounded backlog in memory. On overflow the
        oldest step is dropped and the watcher is told, which is honest — the
        full account is on the run record either way.
        """
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=200)
        self._watchers.setdefault(session_key, set()).add(queue)
        return queue

    def unwatch(self, session_key: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        watchers = self._watchers.get(session_key)
        if not watchers:
            return
        watchers.discard(queue)
        if not watchers:
            self._watchers.pop(session_key, None)

    def _board_changed(self) -> None:
        if self.on_board_change is not None:
            self.on_board_change()

    def _settle(self, run: Run) -> None:
        """Persist the finished run and wake anyone watching it.

        Without this a watcher would sit on its keepalive until the next timeout
        before noticing the run had ended — the wrong end of the interaction to
        be slow at."""
        self._store.finish(run)
        self._publish(run.session_key, {"type": "settled", "status": run.status, "ts": time.time()})
        self._board_changed()

    def _publish(self, session_key: str, event: dict[str, Any]) -> None:
        """Fan one step out to whoever is watching. Never raises: a broken
        watcher is not a reason to disturb the investigation."""
        for queue in tuple(self._watchers.get(session_key, ())):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                    queue.put_nowait({"type": "dropped", "ts": time.time()})
                except (asyncio.QueueEmpty, asyncio.QueueFull):  # pragma: no cover - racing reader
                    pass

    def get(self, session_key: str) -> Run | None:
        return self._store.get(session_key)

    def record_recovery(self, source: str, title: str, *, event_id: Any = None) -> Run | None:
        """The condition an investigation was about has ended. Record it, spend nothing.

        This is the only verification this service can make without a person and
        without running anything: the alert that opened the work is over. It says
        the condition cleared — NOT that the investigation was right, and not
        that the agent caused it. A flapping alert clears on its own, which the
        judge's own self-heal figures already track; the value here is that on an
        unattended deployment "did this end well?" stops being unanswerable.

        Identity is (source, title), which works because the judge strips the
        "it ended" decoration before sending: a recovery and its firing arrive
        here under the same condition name. Unlike `same_alert` below, an
        unresumable or failed run still counts — this is not looking for
        something to continue, it is looking for the work this fact is about.

        Idempotent: the pipe retries deliveries, and a condition ends once.
        """
        cutoff = time.time() - _RECOVERY_WINDOW_SECONDS
        best: Run | None = None
        for run in self._store.list_runs(limit=200):
            meta = run.meta or {}
            if str(meta.get("source") or "") != source or str(meta.get("title") or "") != title:
                continue
            when = run.finished_at or run.created_at
            if when < cutoff:
                continue
            if best is None or when > (best.finished_at or best.created_at):
                best = run
        if best is None:
            return None
        if not best.meta.get("recovered_at"):
            best.meta["recovered_at"] = time.time()
            if event_id is not None:
                best.meta["recovered_by_event"] = event_id
            self._store.annotate(best)
            self._board_changed()
            logger.info("condition recovered session=%s title=%s", best.session_key, title[:80])
        # And the procedures this work ran: the condition ending is the evidence
        # an executed procedure was waiting for. Stamped whenever it arrives —
        # evidence is evidence — and only once, so a retry changes nothing.
        held = remediation.evidence(
            self._settings.workdir, best.session_key, held=True, by="recovery", event_id=event_id
        )
        if held:
            self._board_changed()
            logger.info("remediation held session=%s procedures=%s", best.session_key, ",".join(r["id"] for r in held))
            for row in held:
                self.report_outcome(row, remediation.HELD, str(row.get("held_reason") or ""))
        return best

    def record_refire(self, run: Run, *, event_id: Any = None) -> list[dict[str, Any]]:
        """The condition fired again. For a procedure that ran inside the
        verification window this is the evidence it did NOT hold; nothing is
        run, nothing is retired, the row and the audit simply say so. The
        re-fire itself is handled by the door (a follow-up turn, or nothing
        while one is in flight); this only records what it means for the work
        already done."""
        rows = remediation.evidence(self._settings.workdir, run.session_key, held=False, by="refire", event_id=event_id)
        if rows:
            self._board_changed()
            logger.info(
                "remediation did not hold session=%s procedures=%s", run.session_key, ",".join(r["id"] for r in rows)
            )
            for row in rows:
                self.report_outcome(row, remediation.DID_NOT_HOLD, str(row.get("held_reason") or ""))
        return rows

    def same_alert(self, source: str, title: str, window_seconds: int) -> Run | None:
        """The session already investigating this condition, if one is claimable.

        A running session claims its alert regardless of age — it is live, and
        a re-fire is information for it, not a reason to race it. A finished
        one claims re-fires for `window_seconds` after it finished, so a storm
        extends one investigation instead of funding N cold starts. Identity is
        (source, title): the same pair the case-file recall greps for.
        """
        if window_seconds <= 0 or not title:
            return None
        cutoff = time.time() - window_seconds
        best: Run | None = None
        for run in self._store.list_runs(limit=200):
            meta = run.meta or {}
            if str(meta.get("source") or "") != source or str(meta.get("title") or "") != title:
                continue
            if not run.finished:
                return run
            # Only a session that can actually be reopened is worth claiming
            # with; without an engine session there is nothing to continue.
            claimable = (run.finished_at or 0) >= cutoff and not run.error and run.engine_session_id
            if claimable and (best is None or (run.finished_at or 0) > (best.finished_at or 0)):
                best = run
        return best

    def list_runs(self, limit: int = 100) -> list[Run]:
        return self._store.list_runs(limit=limit)

    def active_count(self) -> int:
        return self._store.active_count()

    def turn_counts(self) -> tuple[int, int]:
        """(turns holding a slot, turns waiting for one)."""
        return self._in_slot, max(0, len(self._running) - self._in_slot)

    def return_failure_count(self) -> int:
        """Runs whose report never reached the pipe — the number an external
        monitor should alert on if the self-alarm URL is not configured."""
        return sum(1 for run in self._store.list_runs(limit=1000) if run.return_status.startswith("failed"))

    def _clamp_timeout(self, raw: Any) -> int:
        try:
            timeout_s = int(raw)
        except (TypeError, ValueError):
            timeout_s = self._settings.default_timeout_seconds
        if timeout_s <= 0:
            timeout_s = self._settings.default_timeout_seconds
        return min(timeout_s, self._settings.max_timeout_seconds)

    async def _execute(self, run: Run, message: str, timeout_s: int, *, resume: str | None = None) -> None:
        logger.info("run start session=%s timeout=%ss resume=%s", run.session_key, timeout_s, resume or "-")
        # Record what the model is about to see, before it sees it: a failed run
        # is exactly when the loaded memory and skills are worth knowing.
        try:
            run.inputs = self._engine.describe_inputs(resume=resume)
            foreign = (run.inputs.get("skills") or {}).get("unrecorded") or []
            if foreign:
                # Loud on purpose: this run is about to load standing
                # instruction that no service write path vouches for.
                logger.warning("skills with no provenance record loaded session=%s: %s", run.session_key, foreign)
        except Exception:  # noqa: BLE001 — a record of the inputs is not worth a failed run
            logger.debug("describe_inputs failed", exc_info=True)
            run.inputs = {}

        def on_event(event: dict[str, Any]) -> None:
            event["ts"] = time.time()
            # Not a step in the investigation — the handle that makes this turn
            # resumable. Checkpointed immediately, because the whole point is to
            # have it on disk BEFORE anything can kill this process.
            if event.get("type") == "session":
                found = str(event.get("id") or "")
                if found and run.engine_session_id != found:
                    run.engine_session_id = found
                    self._store.checkpoint(run)
                return
            # A tool_done is a timing report, not a new step. Matched to its
            # streamed step by tool_use_id it becomes that step's duration; an
            # id the stream never produced is a subagent's call — the message
            # stream only carries the parent's, so this is where subagent work
            # gets into the feed at all.
            if event.get("type") == "tool_done":
                for prior in reversed(run.events):
                    if prior.get("type") == "tool_use" and prior.get("id") == event.get("id"):
                        if "ms" in event:
                            prior["ms"] = event["ms"]
                        if event.get("error"):
                            prior["error"] = True
                        self._publish(run.session_key, event)
                        return
                event = {
                    "type": "tool_use",
                    "sub": True,
                    "id": event.get("id"),
                    "name": event.get("name"),
                    "detail": event.get("detail"),
                    "ts": event["ts"],
                    **({"ms": event["ms"]} if "ms" in event else {}),
                    **({"error": True} if event.get("error") else {}),
                }
            # Deltas are for whoever is watching right now: thousands of them per
            # run, and the finished block that follows says the same thing once.
            # Recording them would bury the process feed and bloat every case file.
            if event.get("type") != "delta":
                run.events.append(event)
                if len(run.events) > 400:  # bound memory and the result file
                    del run.events[: len(run.events) - 400]
            self._publish(run.session_key, event)

        try:
            # The semaphore sits outside the timeout: a queued run's clock
            # starts when it gets a slot, not while it waits for one.
            async with self._semaphore:
                self._in_slot += 1
                try:
                    # The timeout INTERRUPTS before it cancels, for the same
                    # reason Stop does: wait_for() cancelling the coroutine threw
                    # away the SDK's final message, so the priciest failures —
                    # a turn that ran the full clock — recorded no cost at all
                    # and the budget breaker undercounted exactly them.
                    turn = asyncio.ensure_future(
                        self._engine.run(message=message, session_key=run.session_key, resume=resume, on_event=on_event)
                    )
                    try:
                        result = await asyncio.wait_for(asyncio.shield(turn), timeout=timeout_s)
                    except TimeoutError:
                        result = await self._wind_down(turn, timeout_s)
                finally:
                    self._in_slot -= 1
        except TimeoutError:
            self._fail(run, f"timed out after {timeout_s}s")
            return
        except asyncio.CancelledError:
            if run.session_key in self._stop_requested:
                # Operator hit Stop: cancellation IS the intended outcome, so
                # swallow it and let the run settle as an ordinary failure.
                self._stop_requested.discard(run.session_key)
                self._fail(run, "stopped by operator")
                return
            self._fail(run, "cancelled during shutdown")
            raise
        except Exception as exc:  # noqa: BLE001 — the run must always reach a final state
            logger.exception("run crashed session=%s", run.session_key)
            crash = f"{type(exc).__name__}: {exc}"
            if await self._retry_transient(run, crash, None, timeout_s, resume):
                return
            self._fail(run, crash)
            return

        run.message_count = result.message_count
        run.cost_usd = self._turn_cost(result)
        if result.session_id:
            run.engine_session_id = result.session_id
        if result.error:
            # A provider blip is not a verdict on the investigation. One more
            # attempt, now, rather than a failure somebody reads days later.
            if await self._retry_transient(run, result.error, result, timeout_s, resume):
                return
            self._fail(run, result.error, result)
            return

        run.status = COMPLETED
        # Suggestions ride the report as marker lines; lift them into the queue
        # before anything records or delivers the text.
        stripped, facts = suggestions.extract(result.text)
        run.text = stripped
        if facts:
            try:
                memory = suggestions.append(
                    self._settings.workdir,
                    run.session_key,
                    facts,
                    # Capped by the tier: the knob may be on, but memory's
                    # ceiling is what finally decides, so an operator can
                    # halt auto-apply in one config line without hunting the
                    # knob down. Default ceiling is auto_apply, so unchanged.
                    apply_safe=(
                        self._settings.memory_auto_apply
                        and automation.permits(self._settings.automation_tiers, "memory", "auto_apply")
                    ),
                )
                if memory["queued"]:
                    run.meta["memory_suggestions"] = memory["queued"]
                if memory["applied"]:
                    run.meta["memory_applied"] = memory["applied"]
            except OSError:
                logger.warning("could not queue memory suggestions", exc_info=True)
        # Same shape as the suggestions above and for the same reason: the agent
        # PROPOSES in its report and the service holds the credential, because
        # the agent is the component that reads attacker-influenced text.
        run.text, filed = rulings.extract(run.text)
        if filed:
            try:
                # Local first: the gate reads this file, and a judge outage must
                # not also cost the service its own memory of what it ruled.
                rulings.record_local(self._settings.workdir, filed, model=run.model)
            except OSError:
                logger.warning("could not record rulings locally", exc_info=True)
            task = asyncio.create_task(self._file_rulings(run, filed))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        # The other direction of ruling: what this service concludes about its own
        # finished RUNS, not about a condition. Filed locally and marked inferred;
        # it gates nothing, so there is no credential to hold and no far end to
        # reach — see hookprobe.run_rulings for why the two stay apart.
        run.text, run_verdicts = run_rulings.extract(run.text)
        for verdict in run_verdicts:
            try:
                self.record_ruling(
                    str(verdict["sessionKey"]),
                    str(verdict["ruling"]),
                    # Not f"patrol:{key}": a patrol's own session key already
                    # starts with it, and the audit string read patrol:patrol:…
                    actor=_inferred_actor(run.session_key),
                    why=str(verdict["why"]),
                )
            except ValueError:
                logger.warning("refusing an inferred run ruling with an unknown verdict")
            except LookupError:
                logger.info("inferred run ruling names a run this service does not have: %s", verdict["sessionKey"])
        # What stopped it, lifted the same way the procedure is, and annotated
        # with the one thing the AGENT could not see: whether a named credential
        # is absent from this process or merely empty. Those are different
        # repairs — a compose line versus an .env line — and the report that
        # said "the token is empty" three times could not tell them apart.
        gaps = blockers.annotate(blockers.extract(run.text))
        if gaps:
            run.meta["blocked_on"] = gaps
            logger.info(
                "run blocked on %s session=%s",
                ",".join(f"{g['kind']}:{g['name'] or 'probe'}" for g in gaps),
                run.session_key,
            )
        # What it changed, as a reviewable patch. Written to a file rather
        # than onto the meta — a diff is an artifact, not metadata, and meta
        # travels into cards and boards. The meta keeps the bookkeeping; the
        # console links to GET /v1/runs/{key}/patch for the text itself.
        patch = patches.extract(run.text)
        if patch:
            try:
                patch_dir = self._settings.workdir / "patches"
                patch_dir.mkdir(parents=True, exist_ok=True)
                (patch_dir / f"{run.session_key}.patch").write_text(patch + "\n", encoding="utf-8")
                run.meta["patch"] = patches.counts(patch)
                # The clone the diff claims to describe is mounted here, so the
                # claim is checked, not stored: commits carrying this run's key
                # are found and their diff compared with the block.
                # Every name this run's commits could carry, the plan's first:
                # the brief asks the runner to open its message with the PLAN's
                # key, which is not the key of the run doing the committing.
                run.meta["patch"].update(
                    patches.verify(
                        patch,
                        self._settings.workdir / "code",
                        [run.meta.get("work_id"), run.meta.get("session"), run.session_key],
                    )
                )
                logger.info(
                    "run produced a patch +%d/-%d matches_clone=%s commits=%s session=%s",
                    run.meta["patch"]["adds"],
                    run.meta["patch"]["dels"],
                    run.meta["patch"]["matches"],
                    ",".join(run.meta["patch"]["commits"]) or "-",
                    run.session_key,
                )
            except OSError as exc:
                # The report and the commits in the clone are intact; only the
                # review copy is missing. Say so rather than fail the run.
                logger.warning("patch lift failed session=%s: %s", run.session_key, exc)
        steps = remediation.extract(run.text)
        if steps:
            try:
                pending = remediation.pending_duplicate(self._settings.workdir, steps)
                if pending:
                    # One pending proposal per procedure. Five identical read-only
                    # checks were parked in one day on production, each with its
                    # own button and its own 24h clock, for one condition re-firing
                    # every four hours; the sixth would have been the same again.
                    run.meta["remediation_proposal"] = pending
                    run.meta["remediation_proposal_reused"] = True
                    logger.info(
                        "remediation already pending session=%s id=%s steps=%s", run.session_key, pending, len(steps)
                    )
                else:
                    proposal_id = remediation.propose(
                        self._settings.workdir, run.session_key, steps, remediation.cursor(run)
                    )
                    run.meta["remediation_proposal"] = proposal_id
                    logger.info(
                        "remediation proposed session=%s id=%s steps=%s", run.session_key, proposal_id, len(steps)
                    )
            except OSError:
                logger.warning("could not park the remediation proposal", exc_info=True)
        self._record_turn(run, result)
        if run.meta.get("consolidates"):
            # A consolidation run's product is a PROPOSAL beside the manifest,
            # waiting for review — and it must never itself be distilled, or
            # the loop would write runbooks about rewriting runbooks.
            distill_loop.accept_consolidation(run, result, self._settings)
        elif run.meta.get("patrol"):
            # Same rule, one category wider, and it took a real run to notice:
            # the first self-review patrol installed a runbook called
            # `patrol-self-review`. A runbook is loaded as instruction by every
            # later run, so a review OF the loop had just become part of the
            # loop — and the brief that sent it promises in its first paragraph
            # that a run of it writes nothing.
            #
            # Recorded rather than silent, because "the loop did nothing again"
            # is the failure the whole distil feature exists to end.
            run.distilled = {"skipped": "a review of the investigator is not a runbook"}
            logger.info("auto-distill skipped session=%s reason=patrol", run.session_key)
        elif run.meta.get("synthetic"):
            # A drill or a by-hand check produced this report. Three of the twenty
            # runbooks on the production shelf were distilled from exactly such
            # runs — a shell-command test, a chat about a test environment — and
            # were loaded as instruction into every later investigation.
            run.distilled = {"skipped": "a synthetic run teaches nothing"}
            logger.info("auto-distill skipped session=%s reason=synthetic", run.session_key)
        else:
            # After the turn is recorded, because the runbook is assembled from it.
            distill_loop.auto_distill(run, result, self._settings)
            distill_loop.maybe_consolidate(run, self._settings, self._store, self.start)
        self._settle(run)
        self._schedule_return(run)
        logger.info(
            # `messages`, not `turns`: a turn is an entry in run.turns and there
            # is normally one. This is the SDK message count, and calling it
            # turns is how `turns=32294` got read as plausible for a while.
            "run completed session=%s messages=%s turns=%s cost_usd=%s",
            run.session_key,
            result.message_count,
            len(run.turns),
            result.cost_usd,
        )

    def accept_suggestion(self, suggestion_id: str) -> dict[str, Any] | None:
        """Accept one queued memory line. None if it is already resolved or gone.

        A service method rather than the door reaching into settings, for the
        same reason `approve_remediation` is one: the console and the card press
        must take the identical path, or "approved from a card" and "approved
        from the console" become two behaviours that can drift.

        Everything still in this queue was refused by the shape check, so this is
        the one way such a line reaches standing instruction, and it needs a
        person. The card removes the login, not the person.
        """
        return suggestions.resolve(self._settings.workdir, suggestion_id, accept=True)

    def approve_remediation(self, proposal_id: str, note: str = "", actor: str = "") -> dict[str, Any]:
        """The operator's click, and the only path that runs anything. The gate
        checks and the execution are hookprobe.remediation's; what belongs here
        is the task the sequence runs in, because shutdown has to wait for it.

        The freshness cursor is read HERE rather than inside remediation, for
        the reason the allowlist file is passed in rather than found: this is
        the object that holds the runs, and a module that persists proposals
        should not also be reaching for the store to decide about them.
        """
        row = remediation.approve(
            self._settings.workdir,
            proposal_id,
            allowlist=self._settings.remediation_allowlist,
            high_risk_allowlist=self._settings.remediation_high_risk_allowlist,
            note=note,
            actor=actor,
            at=self.proposal_cursor(proposal_id),
            cooldown=self._settings.remediation_cooldown_seconds,
        )
        task = asyncio.create_task(self._apply_remediation(row))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        self._board_changed()
        return row

    async def alarm(self, text: str) -> bool:
        """Say something to the operator around the pipe; True if it went.

        The delivery object owns the channel and its quiet window, and this is
        the one seam other parts of the service reach it through — the selftest
        watch, today. Kept as a method rather than handing the delivery object
        out, because what a caller needs is "tell somebody", not the machinery.
        """
        return await self._returns.alarm(text)

    def proposal_cursor(self, proposal_id: str) -> dict[str, Any]:
        """The condition this proposal is about, as it stands right now.

        Empty when the proposal or its run is gone — which reads as "nothing
        moved" rather than as a refusal, and that is the intended direction. A
        run pruned by retention must not turn a valid procedure into one nobody
        can run; the 24h window already stops anything that old.
        """
        row = remediation.load(self._settings.workdir, proposal_id)
        run = self._store.get(str((row or {}).get("session_key") or "")) if row else None
        return remediation.cursor(run) if run is not None else {}

    # What a notice needs from the run it is about: enough for the pipe to name
    # the alert, put the card in the right conversation and file it under the
    # right work. Everything else on a run's meta describes work this did none of.
    _NOTICE_META = (
        "title",
        "source",
        "level",
        "event_id",
        "kind",
        "work_id",
        "thread_root",
        # The pipe's own handle for the alert and the platform's, so a notice's
        # card is cut against the alert's chain and filed on its page like the
        # report it follows (notify.py copies both).
        "correlation_id",
        "reference",
    )

    def report_superseded(self, proposal_id: str, reason: str) -> Run | None:
        """Say in the chat that a pressed procedure did not run, and why.

        Without this the refusal is decided correctly and lands nowhere. The
        bridge repaints the pressed card "accepted and passed on" the moment the
        PIPE takes the press — it cannot wait for this service, and it has
        already stripped the buttons, whose token is single-use — so by the time
        the cursor refuses, the one surface the operator is looking at has told
        them the opposite. A gate whose refusal is invisible is worse than no
        gate: the procedure did not run AND nobody knows.

        Report-shaped and returned through the family loop, the same way the
        budget breaker answers, because that is the one dialect every channel
        renderer can dress. Relay-born runs only: a console-started run has no
        chat to answer into, and the console shows the row's status directly.
        """
        row = remediation.load(self._settings.workdir, proposal_id)
        if row is None:
            return None
        run = self._store.get(str(row.get("session_key") or ""))
        if run is None:
            return None
        return self._notice(
            run,
            key=f"probe:superseded:{row.get('id')}",
            kind="superseded",
            error=f"not executed: {reason}",
            text=superseded_report(reason, list(row.get("steps") or [])),
            extra={"proposal": str(row.get("id") or "")},
        )

    def report_cooling(self, proposal_id: str, reason: str) -> Run | None:
        """Say in the chat that a pressed procedure was held back, and why.

        Same hole as `report_superseded` and the same answer: the bridge has
        already repainted the card "accepted and passed on" and stripped its
        buttons, so a refusal decided here reaches the operator through no
        existing path. A refusal the refused party cannot see is not a refusal.

        Keyed on the proposal AND on the press, unlike the superseded notice.
        Superseding is terminal — the row can only be retired once, so one
        notice is the whole truth. Cooling is not: the same proposal may be
        pressed again after the window and refused again if something else got
        there first, and collapsing those onto one key would answer the second
        press with silence.
        """
        row = remediation.load(self._settings.workdir, proposal_id)
        if row is None:
            return None
        run = self._store.get(str(row.get("session_key") or ""))
        if run is None:
            return None
        digest = hashlib.sha256(f"{proposal_id}:{reason}".encode()).hexdigest()[:10]
        return self._notice(
            run,
            key=f"probe:cooling:{digest}",
            kind="cooling",
            error=f"not executed: {reason}",
            text=cooling_report(reason, list(row.get("steps") or [])),
            extra={"proposal": str(row.get("id") or "")},
        )

    def report_unanswered(self, run: Run, reason: str, *, message_id: str = "") -> Run | None:
        """Say in the thread that a reply got no answer, and why.

        The same hole as `report_superseded`, found by being asked what happens
        when somebody just keeps typing. Every refusal in the follow-up door is
        a 200 with a reason, and the pipe's own comment says what that buys:
        "the pipe records it". It records it in a LEDGER. The channel records
        nothing, because a 2xx from this service is a delivered delivery — so
        the person who asked the question watches the bot go quiet and has no
        way to learn that it heard them and declined.

        Keyed off the message so the platform's redeliveries collapse onto one
        notice instead of one per attempt.
        """
        digest = hashlib.sha256((message_id or uuid.uuid4().hex).encode()).hexdigest()[:10]
        return self._notice(
            run,
            key=f"probe:unanswered:{digest}",
            kind="unanswered",
            error=f"not answered: {reason}",
            text=unanswered_report(reason),
        )

    def _notice(
        self,
        about: Run,
        *,
        key: str,
        kind: str,
        error: str | None,
        text: str,
        extra: dict[str, Any] | None = None,
        status: str = FAILED,
    ) -> Run | None:
        """One report-shaped message about work, delivered where that work lives.

        Relay-born runs only: a console-started run has no chat to answer into,
        and the console shows the state directly. Idempotent on the key, because
        the callers are all on redelivery or sweep paths. Failure-shaped by
        default — every notice before the outcome one was a refusal — and
        `status=COMPLETED` with no error for the one piece of good news this
        node can bring, a fix that held.
        """
        if about.origin != "relay":
            return None
        if self._store.get(key) is not None:
            return None
        notice = Run(
            session_key=key,
            run_id=uuid.uuid4().hex[:12],
            model=self._settings.model,
            model_endpoint=self._settings.model_endpoint,
            origin="relay",
        )
        # The DELIVERY fields of the original's meta and nothing else, so the
        # card carries the same alert name and lands in the same conversation.
        # Copying the whole dict was wrong and briefly shipped: `refires` and
        # `follow_ups` are counters the work board SUMS across a work item's
        # runs, so a notice carrying them counted every re-fire twice.
        notice.meta = {k: v for k, v in (about.meta or {}).items() if k in self._NOTICE_META and v not in (None, "")}
        # A message, not an investigation. The work board reads this and folds
        # the notice out: it opened no work and answered no question. Left in, a
        # notice was its item's newest FAILED run — reading as `needs_human` on
        # work a recovery had just closed — and it collected a "was this worth
        # it?" the way a real report does.
        notice.meta["notice"] = kind
        notice.meta.update(extra or {})
        self._store.create(notice)
        notice.status = status
        notice.error = error
        notice.cost_usd = 0.0
        notice.text = text
        self._record_turn(notice, None)
        self._store.finish(notice)
        self._schedule_return(notice)
        logger.log(
            logging.WARNING if status == FAILED else logging.INFO,
            "notice %s about=%s reason=%s",
            kind,
            about.session_key,
            error or "good news",
        )
        return notice

    def report_outcome(self, row: dict[str, Any], outcome: str, reason: str) -> Run | None:
        """Say where the report went what became of the procedure it proposed.

        The remediation contract ends with a verdict — held, did not hold — that
        until now lived on the row and the work board and nowhere a person
        reads: the card in the chat said "approved and passed on" and stopped,
        and the pipe's journey of the alert ended at the press. This is the last
        hop: one notice per procedure, keyed on its id, because the evidence is
        stamped once and a window closes once. Good news travels COMPLETED with
        no error; a fix that did not hold is failure-shaped like every other
        notice, and the work board reads it as such.
        """
        if str(row.get("status") or "") != remediation.EXECUTED or row.get("interrupted"):
            return None  # a step that failed was told as itself when it happened; there is nothing to hold
        run = self._store.get(str(row.get("session_key") or ""))
        if run is None or outcome not in (remediation.HELD, remediation.DID_NOT_HOLD):
            return None
        held = outcome == remediation.HELD
        pid = str(row.get("id") or "")
        return self._notice(
            run,
            key=f"probe:outcome:{pid}",
            kind="outcome",
            status=COMPLETED if held else FAILED,
            error=None if held else f"did not hold: {reason}",
            text=outcome_report(outcome, reason, row),
            extra={
                "proposal": pid,
                "outcome": outcome,
                # What decided it: the recovery door, a re-fire, or the window
                # closing with nothing said — the last is the weak form, and
                # the card says so in the same words the row does.
                "held_by": str(row.get("held_by") or "window"),
                "approved_by": str(row.get("approved_by") or ""),
            },
        )

    def sweep_outcomes(self, now: float | None = None) -> int:
        """Every executed procedure whose verdict is in but untold, told once.

        Two of the three verdicts arrive as events and are told at once
        (`record_recovery`, `record_refire`); the third — the window closing
        quietly — arrives as nothing at all, so a clock has to ask. The same
        pass also catches a verdict whose notice never got out (this node was
        down when it was decided). `_notice` is idempotent on the key, so this
        may run as often as it likes.
        """
        told = 0
        for row in remediation.list_all(self._settings.workdir, limit=200):
            if str(row.get("status") or "") != remediation.EXECUTED or row.get("interrupted"):
                continue
            run = self._store.get(str(row.get("session_key") or ""))
            if run is None:
                continue
            verdict, why = remediation.outcome(row, now, recovered_at=run.meta.get("recovered_at"))
            if verdict in (remediation.HELD, remediation.DID_NOT_HOLD) and self.report_outcome(row, verdict, why):
                told += 1
        return told

    def reject_remediation(self, proposal_id: str) -> dict[str, Any]:
        row = remediation.reject(self._settings.workdir, proposal_id)
        self._board_changed()
        return row

    async def _apply_remediation(self, row: dict[str, Any]) -> None:
        await remediation.execute(
            self._settings.workdir,
            row,
            bash_timeout_ms=self._settings.bash_timeout_ms,
            # The second gate needs the file, not the patterns read at the click:
            # an operator narrowing it mid-procedure should stop what has not run.
            allowlist=self._settings.remediation_allowlist,
            high_risk_allowlist=self._settings.remediation_high_risk_allowlist,
            verify_seconds=self._settings.remediation_verify_seconds,
        )
        self._board_changed()

    async def _retry_transient(
        self, run: Run, error: str, result: EngineResult | None, timeout_s: int, resume: str | None
    ) -> bool:
        """One more attempt at a failure that was about the moment, not the request.

        Two real alert investigations died on `API Error: 524` — a gateway
        timeout — and sat in the board's "needs a human" column for four days.
        By the time anybody read it, re-investigating meant paying for a
        question whose answer had stopped mattering. The moment to try again is
        the moment.

        The same three bounds as every other path that spends unasked: one
        retry per turn (counted on the run), the budget breaker, and a
        classification that defaults to "permanent" — `engine.transient` lists
        what is worth trying again, and a context-window limit or an
        insufficient balance is not on it.

        The lost attempt is recorded as its own turn with whatever cost the
        engine reported, so a failure that ran for a minute before the gateway
        gave up is in the ledger rather than erased by the attempt that
        succeeded. Returns True when a replacement turn is now in flight.
        """
        if int(run.meta.get("auto_retries") or 0) >= _MAX_AUTO_RETRIES or not transient(error):
            return False
        state = self.budget_state()
        if state is not None and state[0] >= state[1]:
            return False
        asked = run.current_message
        run.error = error
        run.text = ""
        run.cost_usd = self._turn_cost(result)
        self._record_turn(run, result)
        run.meta["auto_retries"] = int(run.meta.get("auto_retries") or 0) + 1
        resume_id = run.engine_session_id or resume
        message = _RETRY_MESSAGE if resume_id else asked
        run.error = None
        run.status = RUNNING
        run.run_id = uuid.uuid4().hex[:12]
        run.finished_at = None
        run.cost_usd = None
        run.current_message = message
        logger.warning("transient failure, retrying once session=%s error=%s", run.session_key, error[:120])
        self._publish(
            run.session_key,
            {"type": "text", "text": f"provider error, trying once more: {error[:160]}", "ts": time.time()},
        )
        await asyncio.sleep(self.retry_backoff_seconds)
        self._spawn(run, message, timeout_s, resume=resume_id)
        return True

    def _fail(self, run: Run, reason: str, result: EngineResult | None = None) -> None:
        run.status = FAILED
        run.error = reason
        # Whatever the engine managed to say goes into the failure report rather
        # than under it. `run.text` is empty during a turn — the answer is on
        # the result, when there is one.
        run.text = failure_report(reason, produced=result.text if result is not None else "")
        # A failure that got a result still knows its bill; one that was cut off
        # mid-turn — wall clock, crash, Stop — never will, because the engine
        # reports dollars only with its result. Recording None there is the
        # honest answer, and _record_turn says why it is not the same as $0.
        if result is not None:
            run.cost_usd = self._turn_cost(result)
        self._record_turn(run, result)
        self._settle(run)
        self._schedule_return(run)
        self._alarm_if_node_wide(run, reason)
        logger.warning("run failed session=%s reason=%s", run.session_key, reason)

    async def _file_rulings(self, run: Run, filed: list[dict[str, Any]]) -> None:
        """Post each ruling to the judge. Detached, and never costs the report.

        No retry. A ruling is a standing read of evidence that the next patrol
        will produce again next week, so a lost one costs a week rather than a
        fact — and a retry queue for it would be more machinery than the thing is
        worth. The failure is logged with the identity so it is greppable.
        """
        settings = self._settings
        if not settings.ruling_url or not settings.ruling_secret:
            logger.info("rulings not configured; %s dropped for %s", len(filed), run.session_key)
            return
        sent = 0
        for body, headers in rulings.payloads(filed, model=run.model, secret=settings.ruling_secret):
            try:
                await asyncio.to_thread(self._post_ruling, body, headers)
                sent += 1
            except urllib.error.HTTPError as exc:
                logger.warning("ruling refused status=%s body=%s", exc.code, exc.read(200))
            except OSError as exc:
                logger.warning("ruling could not be delivered: %s", exc)
        run.meta["ai_rulings"] = sent
        logger.info("rulings filed session=%s sent=%s of %s", run.session_key, sent, len(filed))

    def _post_ruling(self, body: bytes, headers: dict[str, str]) -> None:
        """One signed POST, on a thread, with urllib.

        urllib and not httpx, and that distinction just cost a red CI. The image
        installs pyproject's THREE runtime dependencies and nothing else, so an
        `import httpx` in runtime code worked only while httpx happened to arrive
        transitively under `mcp`. mcp 2.0 moved to httpx2, httpx stopped arriving,
        and the container stopped booting — ModuleNotFoundError, on a dependency
        bump that touched none of this code.

        hookprobe.notify has always posted this way, for exactly this reason.
        Runtime code here gets the stdlib or one of the declared three.
        """
        request = urllib.request.Request(  # noqa: S310 — operator URL  # nosec B310
            self._settings.ruling_url,
            data=body,
            headers={"Content-Type": "application/json", **headers},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10):  # noqa: S310 — operator URL  # nosec B310
            pass

    def _alarm_if_node_wide(self, run: Run, reason: str) -> None:
        """A failure that is about the NODE says so out loud, once.

        The hourly selftest can only find an outage longer than an hour. The
        gateway was 403 for about thirty-five minutes on 2026-09-22, between
        two ticks: the selftest said `held` on both sides of it and the only
        thing that actually noticed was a run dying mid-investigation, in a log
        nobody reads. Runs are the dense signal — they fail the moment the
        thing breaks — so a node-wide failure now takes the same road the
        selftest's does, around the pipe, behind the same quiet window (one per
        channel, 10 minutes by default, suppressed count folded into the next).

        Detached like the return delivery: nothing on this side is waiting, and
        an alarm must never be able to turn "the run failed" into "the request
        died". `notify.alarm` already refuses to raise; this adds the second
        half, which is that the scheduling cannot either.
        """
        why = unreachable(reason)
        if not why:
            return
        run.meta["node_wide_failure"] = why
        if not self._settings.alarm_url:
            # Recorded, not sent: a node with no channel failing silently is a
            # state an operator has to be able to see on the run itself.
            run.meta["alarm"] = "no channel"
            logger.error("node-wide failure with no alarm channel session=%s why=%s", run.session_key, why)
            return
        text = (
            f"{why}\n"
            f"run: {run.session_key}\n"
            f"error: {reason[:200]}\n"
            "every investigation this node runs would fail the same way until this is fixed"
        )
        task = asyncio.create_task(self._alarm_and_record(run, text))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _alarm_and_record(self, run: Run, text: str) -> None:
        run.meta["alarm"] = "sent" if await self.alarm(text) else "suppressed"
        logger.error(
            "node-wide failure session=%s why=%s alarm=%s",
            run.session_key,
            run.meta.get("node_wide_failure"),
            run.meta["alarm"],
        )

    def _schedule_return(self, run: Run) -> None:
        """The family loop: relay-born runs report back to the pipe. Detached,
        because nobody is waiting on this side — see hookprobe.notify.

        `_meta.notify` opts a run in that the relay did not send. The guard used
        to be origin alone, which is right for the two callers that existed: a
        relay-born run reports back, and a platform-born one is POLLED at
        /final, so returning as well would deliver it twice.

        A patrol is a third case and had neither. Nothing polls it — the crontab
        that fired it is long gone — so its report reached a JSON file on the
        volume and stopped there. Three verified patrol runs cost $2.20 and were
        read by nobody but me, over SSH. A scheduled report with no delivery is
        just a slower way of spending money.
        """
        if not self._settings.return_url:
            return
        if run.origin != "relay" and not run.meta.get("notify"):
            return
        task = asyncio.create_task(self._returns.deliver(run, self._return_delays))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _rates(self) -> tuple[float, float, float, float]:
        return (
            self._settings.price_in_per_1m,
            self._settings.price_cache_read_per_1m,
            self._settings.price_cache_write_per_1m,
            self._settings.price_out_per_1m,
        )

    def _turn_cost(self, result: EngineResult | None) -> float | None:
        """This turn's cost, priced here rather than taken from the runtime.

        One seam for three adapters, which is the reason it lives in the service
        and not in each engine: the Claude CLI reports its own table's estimate
        for a model it is not billing, codex reports no money at all, and pi
        priced from a catalogue this gateway's model is absent from. Every one of
        them reports TOKENS. So when the operator has stated their rates, the
        cost is arithmetic over two measured things and the same arithmetic on
        every runtime — including the two where the budget ceiling previously
        could not bind, because there was no number for it to compare.

        Falls back to whatever the runtime said when no rates are configured,
        so nothing about an existing deployment changes on upgrade.
        """
        if result is None:
            return None
        priced_here = price_tokens(result.usage, self._rates())
        return priced_here if priced_here is not None else result.cost_usd

    def _record_turn(self, run: Run, result: EngineResult | None) -> None:
        run.turns.append(
            {
                "message": run.current_message,
                "text": run.text,
                "error": run.error,
                "run_id": run.run_id,
                # None means "nobody counted", 0.0 means "cost nothing" — a
                # refusal is free, a run the wall clock cut off is not, and the
                # ledger must not read the second as the first. window_spend()
                # can only add up what was reported; window_unpriced() says how
                # many turns are missing from it.
                "cost_usd": run.cost_usd,
                "finished_at": time.time(),
                "usage": result.usage if result else None,
                "model_usage": result.model_usage if result else None,
                "duration_ms": result.duration_ms if result else None,
                "events": list(run.events),
                "inputs": dict(run.inputs),
                # Empty on every healthy run. Stored next to the inputs it is
                # about, so a report and the evidence that the run edited what
                # produced it are never more than one record apart.
                "input_changes": list(result.input_changes) if result else [],
                # How full the context was when this turn ended, and any
                # compaction the runtime did while it ran. The patrol that died
                # on a context-window limit had this number available all along
                # and nothing asked for it.
                "context": result.context if result else None,
                "compactions": list(result.compactions) if result else [],
                # How often the posture refused this turn. Zero on a healthy
                # run; a number here says the run kept asking for what it may
                # not have, which is a different run from one refused once and
                # rephrased — and until now the two looked identical.
                "guard_trips": result.guard_trips if result else 0,
            }
        )
