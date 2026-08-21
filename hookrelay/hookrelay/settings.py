"""Process configuration from environment variables.

Deliberate split: everything OPERATIONAL (tokens, paths, limits) comes from the
environment and differs per deployment; everything about ROUTING (sources,
channels, routes) lives in config.yaml, because routing is versioned content an
operator reviews in git, not a machine detail.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


@dataclass(frozen=True, slots=True)
class Settings:
    # Routing config: sources, sinks and rules. Re-read on demand, not only at boot.
    config_path: str
    # The ledger — every delivery, each attempt and the dead letters.
    db_path: str
    # Extra source and sink plugins, loaded at startup.
    plugins_dir: str
    # Required for silence management; empty disables those endpoints entirely
    # (403), so an unconfigured instance cannot be muted by a stranger.
    admin_token: str
    # Empty leaves /status open — single-operator dev mode. Set it anywhere
    # the port is reachable by others.
    read_token: str
    # Inbound body cap. A larger delivery is refused rather than truncated.
    max_body_bytes: int
    # Attempts per delivery before the row is dead-lettered.
    max_attempts: int
    # How long delivered rows are kept; 0 disables purging entirely.
    retention_days: int  # 0 = keep forever
    # Self-alarm for dead letters (who watches the watchman). Empty = off.
    alarm_url: str
    # Floor between two alarm sends, so a storm cannot page repeatedly.
    alarm_min_interval_seconds: int
    # Per-channel circuit breaker: consecutive failures before the channel is
    # given a rest, and how long that rest lasts.
    breaker_threshold: int
    # How long a tripped sink stays open before one probe attempt.
    breaker_cooldown_seconds: int
    # Delivery-loop tick, in seconds.
    worker_interval_seconds: float
    # Signs the buttons on a notification card and verifies them on the way
    # back. Empty = no card carries an action and /card-action refuses
    # everything, which is the right default: an unsigned button is a URL
    # anyone in the group chat can press on your behalf.
    action_secret: str = ""
    # How long a card's buttons stay pressable.
    action_ttl_seconds: int = 24 * 3600
    # Optional second layer on /card-action: when set, the callback must ALSO
    # carry the family's timestamped signature. Defence in depth for anyone who
    # can put a gateway in front of the IM platform's callback; the action token
    # is the control that applies either way.
    card_callback_secret: str = ""
    # Where a card's action LINK should point. Feishu posts a callback and needs
    # no address; DingTalk and WeCom webhook robots cannot call back at all, so
    # for them an action has to be a URL the operator clicks — and only the
    # deployment knows what its own public address is. Empty = those channels
    # carry no actions, which is the same fail-closed default as an empty
    # action_secret: a link nobody can reach is worse than no link.
    public_url: str = ""

    @classmethod
    def load(cls) -> Settings:
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
            breaker_threshold=_int("HOOKRELAY_BREAKER_THRESHOLD", 5),
            breaker_cooldown_seconds=_int("HOOKRELAY_BREAKER_COOLDOWN_SECONDS", 60),
            worker_interval_seconds=float(os.environ.get("HOOKRELAY_WORKER_INTERVAL", "1.0")),
            action_secret=os.environ.get("HOOKRELAY_ACTION_SECRET", ""),
            action_ttl_seconds=_int("HOOKRELAY_ACTION_TTL_SECONDS", 24 * 3600),
            card_callback_secret=os.environ.get("HOOKRELAY_CARD_CALLBACK_SECRET", ""),
            public_url=os.environ.get("HOOKRELAY_PUBLIC_URL", "").rstrip("/"),
        )
