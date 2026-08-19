"""Self-alarm: who watches the watchman.

When a delivery dead-letters, the thing that would normally carry the news is
exactly the thing that just failed. So this posts ONE minimal message directly
to HOOKRELAY_ALARM_URL — no routing, no templates, no outbox, nothing from the
stack that just broke.

Three disciplines, each learned from the same class of incident:
  - rate limited (HOOKRELAY_ALARM_MIN_INTERVAL_SECONDS): a channel outage
    dead-letters in bursts, and an alarm that storms is an alarm you mute.
  - suppressed count folded into the next message, so throttling never hides
    the scale of the problem.
  - never raises: an alarm failure must not become a delivery failure.
"""

from __future__ import annotations

import httpx


class SelfAlarm:
    def __init__(self, url: str, min_interval_seconds: int) -> None:
        self._url = url.strip()
        self._min_interval = max(0, min_interval_seconds)
        self._last_sent = 0.0
        self._suppressed = 0

    @property
    def enabled(self) -> bool:
        return bool(self._url)

    async def dead_letter(
        self, client: httpx.AsyncClient, *, channel: str, event_id: int, error: str, now: float
    ) -> None:
        if not self.enabled:
            return
        if now - self._last_sent < self._min_interval:
            self._suppressed += 1
            return
        held = self._suppressed
        self._suppressed = 0
        self._last_sent = now
        text = f"[hookrelay] delivery dead-lettered\nchannel: {channel}\nevent: #{event_id}\nreason: {error[:200]}"
        if held:
            text += f"\n({held} more dead letters folded into this one during the quiet window)"
        # Feishu bot text shape doubles as a readable generic JSON body.
        try:
            await client.post(self._url, json={"msg_type": "text", "content": {"text": text}}, timeout=5.0)
        except Exception:  # noqa: BLE001 — an alarm must never raise into delivery
            self._last_sent = 0.0  # let the next dead letter try again
            # The count was cleared on the way in, for a message that never
            # arrived. Give it back, or throttling hides the scale of the
            # problem exactly when the alarm path is also broken — which is the
            # one discipline this class says it keeps.
            self._suppressed += held + 1
