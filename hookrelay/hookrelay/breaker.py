"""Per-channel circuit breaker.

When a channel is wholly down (Feishu unreachable, a bot revoked), every queued
delivery for it will fail. Without a breaker each one burns its full attempt
budget against a wall — noise in the ledger, load on a sick peer, and dead
letters that say "attempt 8" when the real story is "the channel was down".

The breaker DEFERS rather than fails, which keeps the doctrine consistent with
rate limiting: choosing not to send right now is scheduling, not failure, and
must burn no attempt. Deliveries wait for the probe instead of dying.

  closed    — normal.
  open      — after `threshold` consecutive failures; deliveries defer until
              the cooldown expires.
  half-open — cooldown elapsed: exactly ONE delivery is let through as a
              probe. Success closes the breaker; failure re-opens it.

Process-local, like the fuse: it protects, it does not account.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class _State:
    consecutive_failures: int = 0
    opened_at: float | None = None
    probe_in_flight: bool = False


@dataclass
class CircuitBreaker:
    threshold: int = 5
    cooldown_seconds: int = 60
    _states: dict[str, _State] = field(default_factory=dict)

    def _state(self, channel: str) -> _State:
        return self._states.setdefault(channel, _State())

    def allows(self, channel: str, now: float) -> bool:
        """True when a delivery may be attempted right now."""
        state = self._state(channel)
        if state.opened_at is None:
            return True
        if now - state.opened_at < self.cooldown_seconds:
            return False
        # Half-open: one probe at a time, so a queue of 500 does not become 500
        # simultaneous probes against a peer that is still sick.
        if state.probe_in_flight:
            return False
        state.probe_in_flight = True
        return True

    def release_probe(self, channel: str) -> None:
        """Give the probe slot back without judging the channel.

        `allows()` claims the slot, and only record_success/record_failure ever
        returned it — but the caller can still decline to send after being
        allowed (the rate limiter defers the row, and deferring must burn no
        attempt and reach no peer). That path left the slot claimed forever, so
        a channel that had recovered never sent again until a restart: every
        later `allows()` saw a probe in flight and deferred.
        """
        state = self._states.get(channel)
        if state is not None:
            state.probe_in_flight = False

    def record_success(self, channel: str) -> None:
        self._states[channel] = _State()

    def record_failure(self, channel: str, now: float) -> None:
        state = self._state(channel)
        state.probe_in_flight = False
        state.consecutive_failures += 1
        if state.consecutive_failures >= self.threshold:
            state.opened_at = now

    def is_open(self, channel: str, now: float) -> bool:
        state = self._states.get(channel)
        return bool(state and state.opened_at is not None and now - state.opened_at < self.cooldown_seconds)

    def snapshot(self, now: float) -> dict[str, str]:
        """Non-closed channels only — a healthy board stays quiet."""
        out: dict[str, str] = {}
        for channel, state in self._states.items():
            if state.opened_at is None:
                continue
            out[channel] = "open" if now - state.opened_at < self.cooldown_seconds else "half-open"
        return out
