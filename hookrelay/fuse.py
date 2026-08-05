"""The storm fuse: per-door volume protection, two stages.

A fuse is a property of the FUSEBOX, not of application logic — it lives at
the door (per-source config), applies regardless of what the pipeline says,
and counts in process memory: fuses protect, ledgers account, and a fuse that
needs its own database is protecting nothing.

Two stages, one knob (`storm_threshold` per window):

  soft  — count > threshold: the event is still RECORDED (skipped ·
          storm_suppressed, with the count in its trace) but walks no
          pipeline and reaches no channel. The account survives the storm.
  hard  — count > 10 × threshold: HTTP 429 without touching storage. At
          this volume the ledger itself is what the fuse is protecting;
          a per-source rejection counter keeps the tally visible in /status.

Process-local on purpose: a restart resets the window, which for a fuse is
correct behaviour (it is not bookkeeping), and the single-process design
means there is no second counter to disagree with.
"""

from __future__ import annotations

from collections import deque

_HARD_MULTIPLIER = 10


class StormFuse:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = {}
        self._suppressed: dict[str, int] = {}
        self._rejected: dict[str, int] = {}

    def check(self, source: str, threshold: int, window_seconds: int, now: float) -> str:
        """Record one arrival and return "pass" | "suppress" | "reject"."""
        if threshold <= 0:
            return "pass"
        hits = self._hits.setdefault(source, deque())
        cutoff = now - max(1, window_seconds)
        while hits and hits[0] < cutoff:
            hits.popleft()
        hits.append(now)
        count = len(hits)
        if count > threshold * _HARD_MULTIPLIER:
            self._rejected[source] = self._rejected.get(source, 0) + 1
            return "reject"
        if count > threshold:
            self._suppressed[source] = self._suppressed.get(source, 0) + 1
            return "suppress"
        return "pass"

    def window_count(self, source: str) -> int:
        return len(self._hits.get(source, ()))

    def snapshot(self) -> dict[str, dict[str, int]]:
        """Non-zero counters only — a healthy board stays quiet."""
        out: dict[str, dict[str, int]] = {}
        for source in set(self._suppressed) | set(self._rejected):
            out[source] = {
                "suppressed": self._suppressed.get(source, 0),
                "rejected": self._rejected.get(source, 0),
            }
        return out
