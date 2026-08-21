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
    # The ledger — every verdict, its cost, and what it was reused for.
    db_path: str
    # Inbound: the pipe signs its deliveries. Empty = accept unsigned, which is
    # a decision for a private network, never a default to drift into.
    ingest_secret: str
    # /rulings/ai only. Deliberately NOT ingest_secret: that one also opens
    # /events, so a component able to sign for it can forge judgements, and the
    # component that posts rulings is the investigator — the one that reads
    # attacker-influenced text. One door, one credential.
    ruling_secret: str
    # Guards the reads. Empty leaves them open and disables the label write and /labels/export.
    read_token: str
    # Inbound body cap. A larger delivery is refused rather than truncated.
    max_body_bytes: int

    # Outbound: where the judgement goes back to. This service delivers to
    # exactly ONE place — the pipe — because fan-out is the pipe's job.
    return_url: str
    # Outbound HMAC secret; signs the judgement on its way back.
    return_secret: str
    # Attempts on the return leg before the row is dead-lettered.
    return_max_attempts: int
    # Return-leg tick, in seconds.
    worker_interval_seconds: float

    # Direct self-alarm for returns that go DEAD: the pipe is the broken
    # link at that moment, so this posts straight to a bot/collector URL.
    alarm_url: str
    # Floor between two alarm sends, so a storm cannot page repeatedly.
    alarm_min_interval_seconds: int

    # Reuse window: how long one identity's verdict answers for restatements.
    reuse_window_seconds: int
    # How long judged rows are kept; 0 disables purging entirely.
    retention_days: int

    # OpenAI-compatible base. Empty leaves the judge on its rules alone.
    ai_base_url: str
    # Credential for the base above.
    ai_api_key: str
    # Model name sent to that base.
    ai_model: str
    # How long one judgement may take before it falls back to the rules.
    ai_timeout_seconds: float
    # Characters of the alert body sent to the model.
    ai_body_limit: int
    # Input price per 1k tokens, so the ledger can cost a verdict.
    ai_price_in_per_1k: float
    # Output price per 1k tokens, the other half of that sum.
    ai_price_out_per_1k: float
    # schema | tools | object to pin one, anything else (default "auto") to
    # negotiate downwards from the strongest the provider will accept.
    ai_structured_output: str = "auto"

    # Widen reuse from one identity to a whole alert rule. 0 = off, because it
    # changes what a verdict is based on: measure on the eval set before
    # turning it on. Last in the field list so it stays optional.
    rule_reuse_window_seconds: int = 0

    # The rest of the untrusted span in the prompt. `ai_body_limit` bounded the
    # body and nothing else, so a title or a field block could be the whole 256
    # KB the door accepts — verbatim into the prompt, billed per alert.
    # ai_title_limit caps every one-line string (title, source, level, a single
    # field value); ai_fields_limit caps the field block as a whole.
    ai_title_limit: int = 300
    # How many payload fields are offered to the model.
    ai_fields_limit: int = 2000

    # A burst is different rules from one origin inside one window. It was the
    # only window in this service hardcoded in the class that uses it, while
    # every peer — reuse, rule reuse, retention, the alarm's quiet time — is a
    # setting, so it was the one correlation behaviour nobody could tune without
    # a deploy.
    burst_window_seconds: int = 600

    # Where to listen. Read here rather than in __main__ so that "what is this
    # process configured to do" is one object and not one object plus two
    # os.environ calls at the entrypoint — hookprobe's shape, and the better one.
    host: str = "127.0.0.1"
    # Port the service listens on.
    port: int = 8200

    @classmethod
    def load(cls) -> Settings:
        return cls(
            db_path=os.environ.get("HOOKJUDGE_DB", "hookjudge.db"),
            ingest_secret=os.environ.get("HOOKJUDGE_INGEST_SECRET", ""),
            ruling_secret=os.environ.get("HOOKJUDGE_RULING_SECRET", ""),
            read_token=os.environ.get("HOOKJUDGE_READ_TOKEN", ""),
            max_body_bytes=_int("HOOKJUDGE_MAX_BODY_BYTES", 256 * 1024),
            return_url=os.environ.get("HOOKJUDGE_RETURN_URL", ""),
            return_secret=os.environ.get("HOOKJUDGE_RETURN_SECRET", ""),
            return_max_attempts=_int("HOOKJUDGE_RETURN_MAX_ATTEMPTS", 6),
            worker_interval_seconds=_float("HOOKJUDGE_WORKER_INTERVAL", 1.0),
            alarm_url=os.environ.get("HOOKJUDGE_ALARM_URL", ""),
            alarm_min_interval_seconds=_int("HOOKJUDGE_ALARM_MIN_INTERVAL_SECONDS", 600),
            reuse_window_seconds=_int("HOOKJUDGE_REUSE_WINDOW_SECONDS", 3600),
            rule_reuse_window_seconds=_int("HOOKJUDGE_RULE_REUSE_WINDOW_SECONDS", 0),
            retention_days=_int("HOOKJUDGE_RETENTION_DAYS", 30),
            ai_base_url=os.environ.get("HOOKJUDGE_AI_BASE_URL", ""),
            ai_api_key=os.environ.get("HOOKJUDGE_AI_API_KEY", ""),
            ai_model=os.environ.get("HOOKJUDGE_AI_MODEL", "gpt-4o-mini"),
            ai_timeout_seconds=_float("HOOKJUDGE_AI_TIMEOUT_SECONDS", 60.0),
            ai_body_limit=_int("HOOKJUDGE_AI_BODY_LIMIT", 4000),
            ai_title_limit=_int("HOOKJUDGE_AI_TITLE_LIMIT", 300),
            ai_fields_limit=_int("HOOKJUDGE_AI_FIELDS_LIMIT", 2000),
            ai_price_in_per_1k=_float("HOOKJUDGE_AI_PRICE_IN_PER_1K", 0.0),
            ai_price_out_per_1k=_float("HOOKJUDGE_AI_PRICE_OUT_PER_1K", 0.0),
            ai_structured_output=os.environ.get("HOOKJUDGE_AI_STRUCTURED_OUTPUT", "auto"),
            burst_window_seconds=_int("HOOKJUDGE_BURST_WINDOW_SECONDS", 600),
            host=os.environ.get("HOOKJUDGE_HOST", "127.0.0.1"),
            port=_int("HOOKJUDGE_PORT", 8200),
        )
