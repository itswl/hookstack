"""Values a config can carry, and the two ways a typo used to change policy."""

from __future__ import annotations

import pytest

from hookrelay.config import Config, ConfigError


def _config(pipeline: list[object]) -> Config:
    return Config.from_dict(
        {
            "sources": [{"name": "grafana", "secret": "", "title": "{title}"}],
            "channels": [{"name": "sink", "type": "generic", "url": "https://sink.example/in"}],
            "routes": [{"name": "all", "source": "*", "send_to": ["sink"]}],
            "pipeline": pipeline,
        }
    )


def test_an_env_reference_inside_a_header_is_resolved(monkeypatch) -> None:
    """The documented auth header is `Bearer ${BRAIN_KEY}`, not a whole-value
    reference. The anchored resolver sent the literal `${BRAIN_KEY}`, the brain
    answered 401, and fail-open made that an unjudged event with no symptom."""
    monkeypatch.setenv("BRAIN_KEY", "s3cret")
    cfg = _config(
        [
            {
                "type": "http",
                "name": "triage",
                "url": "http://brain:9000/",
                "headers": {"authorization": "Bearer ${BRAIN_KEY}"},
            },
            "routes",
        ]
    )
    stage = next(stage for stage in cfg.pipeline if stage.name == "triage")
    assert stage.options["headers"]["authorization"] == "Bearer s3cret"


def test_a_whole_value_reference_still_resolves(monkeypatch) -> None:
    monkeypatch.setenv("BRAIN_URL", "http://brain:9000/")
    cfg = _config([{"type": "http", "name": "triage", "url": "${BRAIN_URL}"}, "routes"])
    stage = next(stage for stage in cfg.pipeline if stage.name == "triage")
    assert stage.options["url"] == "http://brain:9000/"


def test_an_unknown_on_error_value_is_refused_at_load() -> None:
    """Anything but `drop` used to behave as `pass`, so a typo silently chose
    fail-open for a stage whose author may have meant the opposite."""
    with pytest.raises(ConfigError, match="on_error"):
        _config([{"type": "http", "name": "triage", "url": "http://brain:9000/", "on_error": "dropp"}, "routes"])


def test_the_two_on_error_words_are_accepted() -> None:
    for word in ("pass", "drop"):
        cfg = _config([{"type": "http", "name": "triage", "url": "http://brain:9000/", "on_error": word}, "routes"])
        stage = next(stage for stage in cfg.pipeline if stage.name == "triage")
        assert stage.options["on_error"] == word
