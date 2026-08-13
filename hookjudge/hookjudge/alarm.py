"""Self-alarm: who watches the watchman.

When a judgement's return goes DEAD, the thing that would normally carry the
news — the pipe — is exactly the thing that just failed. So this posts ONE
minimal message directly to HOOKJUDGE_ALARM_URL (a Feishu bot, a collector),
touching nothing on the path that broke.

Same three disciplines as the pipe's own alarm, learned from the same class
of incident: rate limited (a pipe outage dead-letters every verdict, and an
alarm that storms is an alarm you mute); the suppressed count folds into the
next message so throttling never hides the scale; and it never raises — an
alarm failure must not become a worker failure.
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

    async def dead_return(self, client: httpx.AsyncClient, *, title: str, error: str, now: float) -> None:
        if not self.enabled:
            return
        if now - self._last_sent < self._min_interval:
            self._suppressed += 1
            return
        held = self._suppressed
        self._suppressed = 0
        self._last_sent = now
        text = f"[hookjudge] verdict return dead-lettered\nalert: {title[:80]}\nreason: {error[:200]}"
        if held:
            text += f"\n({held} more folded into this one during the quiet window)"
        # Feishu bot text shape doubles as a readable generic JSON body.
        try:
            await client.post(self._url, json={"msg_type": "text", "content": {"text": text}}, timeout=5.0)
        except Exception:  # noqa: BLE001 — an alarm must never raise into the worker
            self._last_sent = 0.0  # let the next dead return try again
