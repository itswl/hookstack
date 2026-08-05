"""Shared fixtures: an in-memory-ish store and a small but real Config."""

from __future__ import annotations

import pytest

from hookrelay.config import Config
from hookrelay.settings import Settings
from hookrelay.store import Store


@pytest.fixture
async def store(tmp_path):
    s = Store(str(tmp_path / "test.db"))
    await s.open()
    yield s
    await s.close()


@pytest.fixture
def cfg() -> Config:
    return Config.from_dict(
        {
            "sources": [
                {
                    "name": "grafana",
                    "secret": "s3cret",
                    "title": "{title}",
                    "body": "{message}",
                    "level": "{state}",
                    "level_map": {"alerting": "high", "ok": "info"},
                    "fields": {"state": "{state}"},
                    "fingerprint_fields": ["title"],
                    "dedup_window_seconds": 120,
                },
                {"name": "ci", "secret": "", "title": "{job}", "body": "{detail}"},
            ],
            "channels": [
                {"name": "feishu-main", "type": "feishu", "url": "https://feishu.example/hook"},
                {"name": "ding-main", "type": "dingtalk", "url": "https://ding.example/hook", "secret": "dsec"},
                {"name": "wecom-main", "type": "wecom", "url": "https://wecom.example/hook"},
                {"name": "mirror", "type": "generic", "url": "https://mirror.example/in", "max_per_minute": 1},
            ],
            "routes": [
                {
                    "name": "high",
                    "source": "*",
                    "when": {"level": ["high"]},
                    "send_to": ["feishu-main", "ding-main"],
                    "priority": 100,
                },
                {"name": "grafana-rest", "source": "grafana", "send_to": ["feishu-main"], "priority": 10},
                {"name": "mirror-all", "source": "*", "send_to": ["mirror"], "priority": 0},
            ],
        }
    )


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        config_path="unused",
        db_path=str(tmp_path / "test.db"),
        admin_token="admin-t",
        read_token="read-t",
        max_body_bytes=256 * 1024,
        max_attempts=3,
        worker_interval_seconds=0.01,
    )
