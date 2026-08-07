"""Prometheus text exposition — no client library, no dependency.

Two kinds of number, kept apart on purpose:

  counters — process-local, monotonic within a process lifetime. A restart
             resets them, which Prometheus handles natively (counter reset
             detection); trying to persist them would make the fusebox need a
             database.
  gauges   — read from the ledger at scrape time (queue depth, silences).
             The DB is already the truth; caching it here would invent a
             second truth to disagree with.

Every series here has a consumer (the estate's Prometheus/Grafana/Alertmanager
already run beside this relay). Instruments without a consumer do not belong.
"""

from __future__ import annotations

from typing import Any

_events: dict[tuple[str, str], int] = {}
_deliveries: dict[tuple[str, str], int] = {}


def record_event(source: str, outcome: str) -> None:
    key = (source, outcome)
    _events[key] = _events.get(key, 0) + 1


def record_delivery(channel: str, result: str) -> None:
    """result: sent | failed | dead | deferred"""
    key = (channel, result)
    _deliveries[key] = _deliveries.get(key, 0) + 1


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def _series(name: str, labels: dict[str, str], value: Any) -> str:
    if labels:
        rendered = ",".join(f'{k}="{_escape(str(v))}"' for k, v in labels.items())
        return f"{name}{{{rendered}}} {value}"
    return f"{name} {value}"


def render(
    *,
    queue: dict[str, int],
    fuse: dict[str, dict[str, int]],
    silences: int,
    retention_days: int,
    breakers: dict[str, str] | None = None,
) -> str:
    lines: list[str] = [
        "# HELP hookrelay_up Relay process is serving.",
        "# TYPE hookrelay_up gauge",
        _series("hookrelay_up", {}, 1),
        "# HELP hookrelay_events_total Events accounted for, by door and outcome.",
        "# TYPE hookrelay_events_total counter",
    ]
    for (source, outcome), count in sorted(_events.items()):
        lines.append(_series("hookrelay_events_total", {"source": source, "outcome": outcome}, count))
    lines += [
        "# HELP hookrelay_deliveries_total Delivery attempts resolved, by channel and result.",
        "# TYPE hookrelay_deliveries_total counter",
    ]
    for (channel, result), count in sorted(_deliveries.items()):
        lines.append(_series("hookrelay_deliveries_total", {"channel": channel, "result": result}, count))
    lines += [
        "# HELP hookrelay_outbox Deliveries currently in each ledger state.",
        "# TYPE hookrelay_outbox gauge",
    ]
    for status in ("queued", "sent", "dead"):
        lines.append(_series("hookrelay_outbox", {"status": status}, int(queue.get(status, 0))))
    lines += [
        "# HELP hookrelay_fuse_total Storm-fuse actions, by door.",
        "# TYPE hookrelay_fuse_total counter",
    ]
    for source, counts in sorted(fuse.items()):
        lines.append(_series("hookrelay_fuse_total", {"source": source, "stage": "soft"}, counts.get("suppressed", 0)))
        lines.append(_series("hookrelay_fuse_total", {"source": source, "stage": "hard"}, counts.get("rejected", 0)))
    lines += [
        "# HELP hookrelay_breaker_open Channels whose breaker is not closed (1 = open, 0 = half-open probe).",
        "# TYPE hookrelay_breaker_open gauge",
    ]
    for channel, state in sorted((breakers or {}).items()):
        lines.append(_series("hookrelay_breaker_open", {"channel": channel}, 1 if state == "open" else 0))
    lines += [
        "# HELP hookrelay_silences_active Silences in force right now.",
        "# TYPE hookrelay_silences_active gauge",
        _series("hookrelay_silences_active", {}, silences),
        "# HELP hookrelay_retention_days Ledger retention window (0 = keep forever).",
        "# TYPE hookrelay_retention_days gauge",
        _series("hookrelay_retention_days", {}, retention_days),
    ]
    return "\n".join(lines) + "\n"
