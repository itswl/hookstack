"""The family loop's last mile: a finished investigation reports back.

A relay-born run has nobody polling it. The pipe delivered an alert to
/hooks/event and went back to waiting, so the report reaches a channel only
because this side pushes it — in the PROCESSED-EVENT dialect
(hookrelay/processed.py), which is the one shape every channel renderer knows
how to dress. Speaking anything else renders as an empty card, which is why the
body is assembled in one place instead of by whoever happens to be delivering.

Delivery retries; when the retries are gone it alarms *around* the pipe rather
than through it. That is the whole design of the self-alarm: the pipe is the
broken link at that moment, so the news must not travel over the path whose
failure it is reporting — it goes straight to a bot/collector URL, touching
nothing on the way that just failed. It is rate limited, folds the suppressed
count into the next message, and never raises, because an alarm that became a
delivery failure would be reporting on itself.

The retry pacing itself stays on the service: a test collapses it, and the
service object is where that knob is reachable.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.request

from hookprobe.reports import report_summary
from hookprobe.runs import Run, RunStore
from hookprobe.settings import Settings
from hookprobe.wire import sign_timestamped

logger = logging.getLogger("hookprobe.notify")


class ReturnDelivery:
    """One service's outbound reports, and the alarm standing behind them."""

    def __init__(self, settings: Settings, store: RunStore) -> None:
        self._settings = settings
        self._store = store
        # Self-alarm throttle state (see _alarm_return_failure).
        self._alarm_last_sent = 0.0
        self._alarm_suppressed = 0

    async def deliver(self, run: Run, delays: tuple[float, ...]) -> None:
        summary = report_summary(run.text)
        alert_title = str(run.meta.get("title") or run.session_key)
        body = json.dumps(
            {
                # The PROCESSED-EVENT dialect (hookrelay/processed.py): the
                # investigator is a brain, and a brain hands the pipe its
                # RESULT in the one shape every channel renderer knows how to
                # dress. Speaking anything else renders as an empty card.
                "meta": {
                    "alert_name": f"{alert_title} · investigation",
                    "source": str(run.meta.get("source") or "hookprobe"),
                    "importance": str(run.meta.get("level") or "medium"),
                    "event_id": run.meta.get("event_id"),
                    "brain": "hookprobe",
                    "timestamp": time.time(),
                    # The loop's own facts, for the pipe's fields and ledger:
                    "session_key": run.session_key,
                    "status": run.status,
                    "cost_usd": run.cost_usd,
                    "error": run.error,
                },
                "analysis": {"summary": summary, "event_type": "investigation"},
                "identity": {"session": run.session_key},
                "report": {"summary": summary, "text": run.text},
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()

        last_error = "unknown"
        for attempt, delay in enumerate(delays, start=1):
            if delay:
                await asyncio.sleep(delay)
            try:
                status = await asyncio.to_thread(self._post_return, body)
                if 200 <= status < 300:
                    run.return_status = "sent"
                    self._store.finish(run)
                    logger.info("return delivered session=%s status=%s", run.session_key, status)
                    return
                last_error = f"HTTP {status}"
            except OSError as exc:
                last_error = str(exc) or type(exc).__name__
            logger.warning(
                "return delivery attempt %s failed session=%s error=%s", attempt, run.session_key, last_error
            )
        run.return_status = f"failed: {last_error}"
        self._store.finish(run)
        await self._alarm_return_failure(run, last_error)

    async def _alarm_return_failure(self, run: Run, error: str) -> None:
        """Who watches the watchman: the pipe is the broken link right now, so
        the news travels around it — straight to a bot/collector URL. Rate
        limited, the suppressed count folds into the next message, and it
        never raises: an alarm failure must not become a delivery failure."""
        if not self._settings.alarm_url:
            return
        now = time.time()
        if now - self._alarm_last_sent < self._settings.alarm_min_interval_seconds:
            self._alarm_suppressed += 1
            return
        held = self._alarm_suppressed
        self._alarm_suppressed = 0
        self._alarm_last_sent = now
        text = f"[hookprobe] report return failed\nsession: {run.session_key}\nreason: {error[:200]}"
        if held:
            text += f"\n({held} more folded into this one during the quiet window)"
        body = json.dumps({"msg_type": "text", "content": {"text": text}}, ensure_ascii=False).encode()
        try:
            await asyncio.to_thread(self._post_alarm, body)
        except Exception:  # noqa: BLE001 — an alarm must never raise into delivery
            self._alarm_last_sent = 0.0  # let the next failure try again

    def _post_alarm(self, body: bytes) -> None:
        request = urllib.request.Request(
            self._settings.alarm_url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5):  # noqa: S310 — operator URL  # nosec B310
            pass

    def _post_return(self, body: bytes) -> int:
        headers = {"Content-Type": "application/json", **sign_timestamped(self._settings.return_secret, body)}
        request = urllib.request.Request(self._settings.return_url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310 — operator URL  # nosec B310
            return int(response.status)
