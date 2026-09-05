"""What the service is doing, and what it was told to do — the read-only surface.

Nothing here changes anything. Every route answers a question an operator asks
while something is wrong, and the questions are different enough that they need
different doors:

* /v1/audit — the flight recorder: one line per tool call, across all runs. The
  run's own feed is capped and lives on the run record; this is the greppable
  account of who ran what, when, for which session.
* /v1/config — the knobs as this process actually resolved them, redacted. Secret
  VALUES never appear; a boolean says whether one is set. "Is the secret
  configured" is the question worth answering, and it is answerable without
  handing the answer to whoever asked.
* /v1/mcp — what the NEXT run would load, read fresh from the file rather than
  from what this process started with. Env values are secrets (API tokens live
  there) and are never returned; the key names alone prove the wiring.
* /v1/budget — the spend either way, ceiling or no ceiling. Knowing the cost and
  capping it are different questions, and an operator wants the first answered
  even when they declined to ask the second. It also carries the other half of
  that arithmetic: how many of the window's investigations a person said found
  the cause. Cost was countable from the start and worth was countable nowhere,
  which left "you want me to pay a model per alert?" with a dollar figure and no
  answer to put beside it.
* /metrics and /healthz — the two the machines read, and the only routes here
  without a bearer token. The scraper is on the private network and nothing in
  them is more sensitive than counts and a dollar figure. That is not a
  concession: this platform's own deliveries once failed silently for eight days,
  and a service with no scrape target has the same blind spot by construction.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from fastapi import Depends, FastAPI
from fastapi.responses import PlainTextResponse

from hookprobe import __version__
from hookprobe.engine import _load_mcp_servers, _system_prompt_append
from hookprobe.files import system_prompt_path
from hookprobe.service import RunService
from hookprobe.settings import Settings


def _worth_line(investigations: int, spent: float, useful: int, useless: int, inferred: int = 0) -> str:
    """The one sentence the adoption question actually asks for.

    Assembled here rather than on the board, which prints the string as it
    arrives: the same reason the bearer dependency is defined once and handed
    around. A sentence written in two places is a sentence that will eventually
    say two things, and this one is quoted at people deciding whether to pay.

    Which is why it now distinguishes an absence from a verdict. It read

        3 investigations, $6.29, 0 found the cause

    on a deployment where 108 investigations were unruled and none had ever been
    ruled useless — so the figure quoted at somebody deciding whether to pay was
    "we spent this and got nothing", and it meant "nobody has said". The API
    beside it has always had this right: `ruled_useful` and `ruled_useless` are
    separate, and the comment there says an unruled investigation is unrated,
    never not-useful. Only the rendered sentence collapsed them.

    Same defect as `mattered: 0` beside `likely_flapping` on the judge's board,
    and the same fix: say how many were RULED, and when none were, say that.
    """
    head = f"{investigations} investigation{'' if investigations == 1 else 's'}, ${spent:.2f}"
    ruled = useful + useless
    if not ruled:
        return f"{head} — none ruled yet"
    # And say when the verdicts are a patrol's inference rather than a person's.
    # Leaving that out repeats this function's own founding mistake one level up:
    # the sentence read as somebody's judgement while meaning the model's, which
    # is worse than reading as an absence, because it looks like an answer.
    tail = f", {useful} of {ruled} ruled found the cause"
    if inferred:
        tail += f" ({inferred} inferred, not a person's)" if inferred < ruled else " (all inferred, not a person's)"
    return head + tail


def register(app: FastAPI, settings: Settings, service: RunService, guard: Callable[..., None]) -> None:
    """Mount the observability routes. /metrics and /healthz stay unauthenticated."""

    @app.get("/v1/audit", dependencies=[Depends(guard)])
    async def audit_tail(limit: int = 200, session: str = "") -> dict[str, Any]:
        """The flight recorder, newest FIRST. Reads the most recent day files
        only — the full history stays on disk for grep.

        Newest first because every other list in this family is: run list, skill
        history, the review queue, open proposals, waiting memory lines. This one
        was newest-last because that is the order a JSONL file is appended in, and
        the append order leaked all the way to the page — where it was labelled
        rather than fixed, so 196 rows meant scrolling to the bottom to see what
        just happened.

        `limit` slices the TAIL and then reverses. Slicing the head instead —
        `entries[:limit]` — reads identically and serves the oldest rows under a
        label promising the newest, which is the version of this bug that would
        survive review.
        """
        limit = max(1, min(1000, limit))
        audit_dir = settings.workdir / "audit"
        entries: list[dict[str, Any]] = []
        if audit_dir.is_dir():
            for path in sorted(audit_dir.glob("*.jsonl"))[-3:]:
                for line in path.read_text(encoding="utf-8").splitlines():
                    try:
                        entry = json.loads(line)
                    except ValueError:
                        continue
                    if session and session not in str(entry.get("session") or ""):
                        continue
                    entries.append(entry)
        newest = entries[-limit:]
        newest.reverse()
        return {"entries": newest, "count": len(newest)}

    @app.get("/v1/config", dependencies=[Depends(guard)])
    async def config_view() -> dict[str, Any]:
        """The operational knobs, redacted: secret VALUES never appear —
        booleans say whether they are set."""
        prompt_path = system_prompt_path(settings)
        return {
            "model": settings.model,
            "max_turns": settings.max_turns,
            "max_concurrent": settings.max_concurrent,
            "default_timeout_seconds": settings.default_timeout_seconds,
            "max_timeout_seconds": settings.max_timeout_seconds,
            "workdir": str(settings.workdir),
            "setting_sources": list(settings.setting_sources),
            "skills_filter": settings.skills or None,
            "escalate_levels": sorted(settings.escalate_levels),
            "budget_usd": settings.budget_usd or None,
            "budget_window_hours": settings.budget_window_hours,
            "retention_days": settings.retention_days or None,
            "system_prompt": {"path": str(prompt_path), "active": bool(_system_prompt_append(settings))},
            "agents_config": str(settings.agents_config) if settings.agents_config else None,
            "mcp_config": str(settings.mcp_config) if settings.mcp_config else None,
            "return_url": settings.return_url or None,
            # The URL, not just a boolean: an operator reading this page
            # needs to see WHICH door a hand-off would go through, and the
            # sessions page uses its presence to decide whether to offer
            # the button at all. Its secret stays a boolean, like the rest.
            "handoff_url": settings.handoff_url or None,
            "handoff_secret_set": bool(settings.handoff_secret),
            "bash_guard": settings.bash_guard,
            "alarm_configured": bool(settings.alarm_url),
            "event_secret_set": bool(settings.event_secret),
            "return_secret_set": bool(settings.return_secret),
            "token_required": bool(settings.token),
        }

    @app.get("/v1/mcp", dependencies=[Depends(guard)])
    async def mcp_servers() -> dict[str, Any]:
        """What the next run would load — read fresh from the config file.

        Env VALUES are secrets (API tokens live there) and are never
        returned; the key names alone prove the wiring."""
        servers = _load_mcp_servers(settings.mcp_config, include_disabled=True)
        described: dict[str, Any] = {}
        for name, spec in servers.items():
            if not isinstance(spec, dict):
                described[name] = {"invalid": True}
                continue
            described[name] = {
                key: spec.get(key) for key in ("command", "args", "type", "url") if spec.get(key) is not None
            }
            described[name]["env_keys"] = sorted((spec.get("env") or {}).keys())
            if spec.get("enabled") is False:
                described[name]["disabled"] = True
        return {
            "config": str(settings.mcp_config) if settings.mcp_config else None,
            "servers": described,
        }

    @app.get("/v1/budget", dependencies=[Depends(guard)])
    async def budget() -> dict[str, Any]:
        """The breaker's arithmetic: window spend, ceiling, cache ratio and the worth line."""
        # The spend is reported either way: "what has this cost" is a question
        # worth answering even for an operator who declined to set a ceiling.
        fresh, cached = service.window_cache()
        spend = service.window_spend()
        investigations, useful, useless, inferred = service.window_rulings()
        window = {
            "window_hours": settings.budget_window_hours,
            "spent_usd": round(spend, 6),
            # The prefix an investigation pays for is the harness's, not ours,
            # so the only lever left is reuse — which means this is the number
            # to watch. Reads over reads-plus-fresh: the provider caches
            # implicitly here, so cache writes are always zero.
            "input_tokens": fresh,
            "cache_read_tokens": cached,
            "cache_hit_ratio": round(cached / (cached + fresh), 4) if (cached + fresh) else None,
            # How many turns in the window spent money the engine never got to
            # report — a wall-clock timeout is the most expensive turn there is
            # and the one that bills nothing. Non-zero means the figure above is
            # a floor.
            "unpriced_turns": service.window_unpriced(),
            # Was any of it worth it. Only a person can say, so these stay 0
            # until somebody rules on a report — an unruled investigation is
            # unrated, never "not useful".
            "investigations": investigations,
            "ruled_useful": useful,
            "ruled_useless": useless,
            # Of the ruled ones, how many a patrol inferred rather than a person
            # filed. Separated because this figure is quoted at somebody deciding
            # whether to pay, and a model's opinion of itself is not an answer.
            "ruled_inferred": inferred,
            "worth": _worth_line(investigations, spend, useful, useless, inferred),
        }
        state = service.budget_state()
        if state is None:
            return {"enabled": False, **window}
        spent, limit = state
        return {
            "enabled": True,
            "budget_usd": limit,
            **window,
            "remaining_usd": round(max(0.0, limit - spent), 6),
            "exhausted": spent >= limit,
        }

    @app.get("/metrics")
    async def metrics() -> Any:
        """Prometheus text format, rendered by hand — three gauges are not a
        client-library dependency.

        Unauthenticated like /healthz, and for the same reason: the scraper is
        a machine on the private network, and nothing here is more sensitive
        than counts and a dollar figure. This is how the platform's existing
        alerting finally sees the investigator — the platform's own deliveries
        once failed silently for 8 days, and this service had the same blind
        spot until now.
        """
        running, queued = service.turn_counts()
        lines = [
            "# TYPE hookprobe_up gauge",
            "hookprobe_up 1",
            "# TYPE hookprobe_active_runs gauge",
            f"hookprobe_active_runs {service.active_count()}",
            "# TYPE hookprobe_running_turns gauge",
            f"hookprobe_running_turns {running}",
            "# TYPE hookprobe_queued_turns gauge",
            f"hookprobe_queued_turns {queued}",
            "# TYPE hookprobe_window_spend_usd gauge",
            f"hookprobe_window_spend_usd {service.window_spend():.6f}",
            # Turns whose cost nobody counted, beside the spend they are missing
            # from. The engine reports dollars only on a ResultMessage, and a
            # wall-clock timeout cancels the query before one arrives — so that
            # turn records None, meaning "nobody counted", never 0.0 meaning
            # "free". window_spend is therefore a floor, and this is how far
            # below the real figure it might be.
            #
            # As a SERIES rather than only a number on /v1/budget, because the
            # decision it feeds is a ratio over time: moving the engine to
            # ClaudeSDKClient + interrupt() would recover these dollars exactly,
            # and it is a rewrite at the one boundary no test touches. Worth it
            # if this line is a real fraction of the turns; not worth it if it
            # is flat at zero. Let the graph answer that instead of a guess.
            "# TYPE hookprobe_unpriced_turns gauge",
            f"hookprobe_unpriced_turns {service.window_unpriced()}",
        ]
        budget = service.budget_state()
        if budget is not None:
            lines += [
                "# TYPE hookprobe_budget_usd gauge",
                f"hookprobe_budget_usd {budget[1]:.6f}",
                "# TYPE hookprobe_budget_exhausted gauge",
                f"hookprobe_budget_exhausted {1 if budget[0] >= budget[1] else 0}",
            ]
        try:
            runbooks = len(list((settings.workdir / ".claude" / "skills").glob("*/SKILL.md")))
        except OSError:
            runbooks = 0
        lines += ["# TYPE hookprobe_runbooks gauge", f"hookprobe_runbooks {runbooks}"]
        return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        """Liveness only — the engine is not consulted, so a stuck run cannot hide a live process."""
        running, queued = service.turn_counts()
        return {
            "status": "ok",
            "version": __version__,
            "active_runs": service.active_count(),
            # Slot arithmetic: `running` holds a semaphore slot; `queued` is
            # started but waiting for one. A storm shows up here first.
            "running_turns": running,
            "queued_turns": queued,
            # Reports that never reached the pipe — alert on this going up.
            "return_failures": service.return_failure_count(),
        }
