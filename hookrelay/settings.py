"""Process configuration from environment variables.

Deliberate split: everything OPERATIONAL (tokens, paths, limits) comes from the
environment and differs per deployment; everything about ROUTING (sources,
channels, routes) lives in config.yaml, because routing is versioned content an
operator reviews in git, not a machine detail.
"""

import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


@dataclass(frozen=True, slots=True)
class Settings:
    config_path: str
    db_path: str
    plugins_dir: str
    # Required for silence management; empty disables those endpoints entirely
    # (403), so an unconfigured instance cannot be muted by a stranger.
    admin_token: str
    # Empty leaves /status open — single-operator dev mode. Set it anywhere
    # the port is reachable by others.
    read_token: str
    max_body_bytes: int
    max_attempts: int
    retention_days: int  # 0 = keep forever
    # Self-alarm for dead letters (who watches the watchman). Empty = off.
    alarm_url: str
    alarm_min_interval_seconds: int
    worker_interval_seconds: float

    @classmethod
    def load(cls) -> "Settings":
        return cls(
            config_path=os.environ.get("HOOKRELAY_CONFIG", "config.yaml"),
            db_path=os.environ.get("HOOKRELAY_DB", "hookrelay.db"),
            plugins_dir=os.environ.get("HOOKRELAY_PLUGINS", "plugins"),
            admin_token=os.environ.get("HOOKRELAY_ADMIN_TOKEN", ""),
            read_token=os.environ.get("HOOKRELAY_READ_TOKEN", ""),
            max_body_bytes=_int("HOOKRELAY_MAX_BODY_BYTES", 256 * 1024),
            max_attempts=_int("HOOKRELAY_MAX_ATTEMPTS", 8),
            retention_days=_int("HOOKRELAY_RETENTION_DAYS", 14),
            alarm_url=os.environ.get("HOOKRELAY_ALARM_URL", ""),
            alarm_min_interval_seconds=_int("HOOKRELAY_ALARM_MIN_INTERVAL_SECONDS", 600),
            worker_interval_seconds=float(os.environ.get("HOOKRELAY_WORKER_INTERVAL", "1.0")),
        )
