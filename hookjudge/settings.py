"""Process configuration from the environment. One flat object, no layers."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


@dataclass(frozen=True, slots=True)
class Settings:
    db_path: str
    # Inbound: the pipe signs its deliveries. Empty = accept unsigned, which is
    # a decision for a private network, never a default to drift into.
    ingest_secret: str
    read_token: str
    max_body_bytes: int

    # Outbound: where the judgement goes back to. This service delivers to
    # exactly ONE place — the pipe — because fan-out is the pipe's job.
    return_url: str
    return_secret: str
    return_max_attempts: int
    worker_interval_seconds: float

    # Reuse window: how long one identity's verdict answers for restatements.
    reuse_window_seconds: int
    retention_days: int

    ai_base_url: str
    ai_api_key: str
    ai_model: str
    ai_timeout_seconds: float
    ai_body_limit: int
    ai_price_in_per_1k: float
    ai_price_out_per_1k: float

    @classmethod
    def load(cls) -> Settings:
        return cls(
            db_path=os.environ.get("HOOKJUDGE_DB", "hookjudge.db"),
            ingest_secret=os.environ.get("HOOKJUDGE_INGEST_SECRET", ""),
            read_token=os.environ.get("HOOKJUDGE_READ_TOKEN", ""),
            max_body_bytes=_int("HOOKJUDGE_MAX_BODY_BYTES", 256 * 1024),
            return_url=os.environ.get("HOOKJUDGE_RETURN_URL", ""),
            return_secret=os.environ.get("HOOKJUDGE_RETURN_SECRET", ""),
            return_max_attempts=_int("HOOKJUDGE_RETURN_MAX_ATTEMPTS", 6),
            worker_interval_seconds=_float("HOOKJUDGE_WORKER_INTERVAL", 1.0),
            reuse_window_seconds=_int("HOOKJUDGE_REUSE_WINDOW_SECONDS", 3600),
            retention_days=_int("HOOKJUDGE_RETENTION_DAYS", 30),
            ai_base_url=os.environ.get("HOOKJUDGE_AI_BASE_URL", ""),
            ai_api_key=os.environ.get("HOOKJUDGE_AI_API_KEY", ""),
            ai_model=os.environ.get("HOOKJUDGE_AI_MODEL", "gpt-4o-mini"),
            ai_timeout_seconds=_float("HOOKJUDGE_AI_TIMEOUT_SECONDS", 60.0),
            ai_body_limit=_int("HOOKJUDGE_AI_BODY_LIMIT", 4000),
            ai_price_in_per_1k=_float("HOOKJUDGE_AI_PRICE_IN_PER_1K", 0.0),
            ai_price_out_per_1k=_float("HOOKJUDGE_AI_PRICE_OUT_PER_1K", 0.0),
        )
