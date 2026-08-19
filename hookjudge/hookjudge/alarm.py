"""Self-alarm: who watches the watchman.

Two failures cannot be reported through the pipe, for opposite reasons. When a
judgement's return goes DEAD, the thing that would normally carry the news — the
pipe — is exactly the thing that just failed. When an alert is accepted and then
judged NOWHERE, the pipe is fine and has already been told 202, so it is waiting
for nothing and will never ask again. Both post ONE minimal message directly to
HOOKJUDGE_ALARM_URL (a Feishu bot, a collector), touching nothing on the path
that broke.

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
        await self._send(
            client, f"[hookjudge] verdict return dead-lettered\nalert: {title[:80]}\nreason: {error[:200]}", now
        )

    async def lost_alert(self, client: httpx.AsyncClient, *, title: str, error: str, now: float) -> None:
        """An alert that was answered 202 and then judged nowhere.

        Quieter than a dead return and worse: a dead letter at least leaves a row
        in the ledger saying so, and the sender's own retry is spent. Here the
        judging itself failed after the door had already promised to handle it,
        so there is no row, no retry, and nobody upstream still holding the
        alert. Its own message rather than a reuse of dead_return, because an
        alarm that names the wrong failure sends the reader to the wrong place —
        the return leg is not broken in this one. It shares the quiet window,
        because there is one channel and one person at the end of it.
        """
        await self._send(
            client,
            f"[hookjudge] alert accepted then LOST — no verdict was recorded\n"
            f"alert: {title[:80]}\nreason: {error[:200]}",
            now,
        )

    async def _send(self, client: httpx.AsyncClient, text: str, now: float) -> None:
        """The three disciplines in one place, so a second alarm cannot keep two
        of them and quietly drop the third."""
        if not self.enabled:
            return
        if now - self._last_sent < self._min_interval:
            self._suppressed += 1
            return
        held = self._suppressed
        self._suppressed = 0
        self._last_sent = now
        if held:
            text += f"\n({held} more folded into this one during the quiet window)"
        # Feishu bot text shape doubles as a readable generic JSON body.
        try:
            await client.post(self._url, json={"msg_type": "text", "content": {"text": text}}, timeout=5.0)
        except Exception:  # noqa: BLE001 — an alarm must never raise into the worker
            self._last_sent = 0.0  # let the next dead return try again
            # The count was cleared on the way in, for a message that never
            # arrived. Give it back, or throttling hides the scale of the
            # problem exactly when the alarm path is also broken — which is the
            # one discipline this class says it keeps.
            self._suppressed += held + 1
