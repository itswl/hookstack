"""scripts/deploy_preflight.py refuses a production .env that would open a door.

The composes default most secrets to empty so a laptop can boot with nothing;
on the production host that default opens the door quietly. The preflight reads
the deployed files and the real .env and lists every hop that would come up
unsigned or unauthenticated. Exceptions are written where the deploy reads them.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "deploy_preflight", Path(__file__).resolve().parents[2] / "scripts" / "deploy_preflight.py"
)
preflight = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(preflight)

PIPE = """
sources:
  - name: ww
    secret: ${WW_RELAY_SECRET}
  - name: judge-notify
    secret: ${SHADOW_RETURN_SECRET}
  - name: probe-notify
    secret: ${HOOKPROBE_RETURN_SECRET}
channels:
  - name: to-me
    secret: ""
"""
COMPOSE = """
services:
  hookrelay:
    environment:
      WW_RELAY_SECRET: ${WW_RELAY_SECRET:?set it}
      HOOKRELAY_READ_TOKEN: ${SHADOW_READ_TOKEN:-}
      HOOKPROBE_RETURN_SECRET: ${HOOKPROBE_RETURN_SECRET:-}
      OTEL_EXPORTER_OTLP_ENDPOINT: ${OTEL_EXPORTER_OTLP_ENDPOINT:-}
      HOOKJUDGE_AI_MODEL: ${HOOKJUDGE_AI_MODEL:-deepseek-chat}
      HOOKJUDGE_AI_BASE_URL: ${HOOKJUDGE_AI_BASE_URL:-https://api.deepseek.com/v1}
      HOOKJUDGE_AI_API_KEY: ${HOOKJUDGE_AI_API_KEY:-${ANTHROPIC_AUTH_TOKEN:-}}
      HOOKPROBE_MODEL: ${HOOKPROBE_MODEL:-claude-opus-5}
      ANTHROPIC_MODEL: ${ANTHROPIC_MODEL:-}
"""
# Every secret set and no model stated: what rule 3 alone reacts to.
SECRETS_ONLY = "WW_RELAY_SECRET=a\nSHADOW_RETURN_SECRET=b\nHOOKPROBE_RETURN_SECRET=c\nSHADOW_READ_TOKEN=d\n"
FULL_ENV = (
    "WW_RELAY_SECRET=a\nSHADOW_RETURN_SECRET=b\nHOOKPROBE_RETURN_SECRET='c'\nSHADOW_READ_TOKEN=d\n"
    "HOOKJUDGE_AI_MODEL=gpt-5.6-luna\nHOOKJUDGE_AI_BASE_URL=http://gateway/v1\n"
    "HOOKPROBE_MODEL=gpt-5.6-luna\n"
)


def _write(tmp_path: Path, env: str) -> Path:
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy" / "shadow.yaml").write_text(PIPE)
    (tmp_path / "deploy" / "docker-compose.shadow.yml").write_text(COMPOSE)
    (tmp_path / "hookprobe" / "deploy").mkdir(parents=True)
    (tmp_path / "hookprobe" / "deploy" / "docker-compose.prod.yml").write_text("services: {}\n")
    (tmp_path / ".env").write_text(env)
    return tmp_path


def test_a_complete_env_passes(tmp_path, capsys) -> None:
    root = _write(tmp_path, FULL_ENV)
    assert preflight.main(["x", str(root)]) == 0
    assert "every door signed" in capsys.readouterr().out


def test_an_empty_or_missing_secret_names_the_door_and_the_variable(tmp_path, capsys) -> None:
    root = _write(tmp_path, "WW_RELAY_SECRET=a\nSHADOW_RETURN_SECRET=\nSHADOW_READ_TOKEN=d\n")
    assert preflight.main(["x", str(root)]) == 1
    err = capsys.readouterr().err
    assert "door 'judge-notify': ${SHADOW_RETURN_SECRET} is empty" in err
    assert "door 'probe-notify': ${HOOKPROBE_RETURN_SECRET} is empty" in err
    assert "docker-compose.shadow.yml: ${HOOKPROBE_RETURN_SECRET} is empty" in err
    assert "OTEL_EXPORTER" not in err, "only secrets and tokens are the preflight's business"


def test_a_literal_empty_door_is_refused_even_with_a_full_env(tmp_path, capsys) -> None:
    root = _write(tmp_path, FULL_ENV)
    (root / "deploy" / "shadow.yaml").write_text(PIPE.replace("${HOOKPROBE_RETURN_SECRET}", '""'))
    assert preflight.main(["x", str(root)]) == 1
    assert "literal empty string" in capsys.readouterr().err


def test_an_exception_is_recorded_where_the_deploy_reads_it(tmp_path, monkeypatch) -> None:
    # Everything set but the one token under test, so the exception is what
    # decides the outcome rather than a second finding.
    root = _write(tmp_path, FULL_ENV.replace("SHADOW_READ_TOKEN=d\n", ""))
    assert preflight.main(["x", str(root)]) == 1, "SHADOW_READ_TOKEN is missing"
    monkeypatch.setenv("DEPLOY_ALLOW_EMPTY", "SHADOW_READ_TOKEN")
    assert preflight.main(["x", str(root)]) == 0


def test_a_brain_taking_its_vendor_from_a_compose_default_stops_the_deploy(tmp_path, capsys) -> None:
    """Rules 1 and 2 catch a hop that would come up open; this catches one that
    would come up pointed somewhere else.

    The shadow compose defaults its brain to `deepseek-chat` at
    `api.deepseek.com`, which was a comparison arm's fallback until 2026-09-09.
    The arms were retired that day, so it is now the only brain: a variable
    dropped from .env would put every verdict on a vendor nobody chose, and
    nothing anywhere would print a line about it.
    """
    root = _write(tmp_path, SECRETS_ONLY)
    assert preflight.main(["x", str(root)]) == 1
    err = capsys.readouterr().err
    assert "${HOOKJUDGE_AI_MODEL} is missing from .env" in err
    assert "'deepseek-chat'" in err and "a vendor chosen by a file" in err
    assert "${HOOKJUDGE_AI_BASE_URL} is missing" in err and "api.deepseek.com" in err
    # Not just the judge's spelling. The first version of this rule matched
    # `_AI_MODEL` and so caught the service whose incident prompted it and
    # missed `HOOKPROBE_MODEL`, which is the same hazard on the investigator and
    # the only one still live in a deployed compose.
    assert "${HOOKPROBE_MODEL} is missing from .env" in err and "'claude-opus-5'" in err
    # The key falls back to another VARIABLE, not to a vendor, and an empty key
    # fails loudly at the first call — so it is rule 2's business, not rule 3's.
    assert "HOOKJUDGE_AI_API_KEY" not in err
    # An empty default substitutes nothing, so it cannot answer as somebody else.
    assert "ANTHROPIC_MODEL" not in err


def test_the_provider_rule_takes_the_same_recorded_exception_as_the_others(tmp_path, capsys, monkeypatch) -> None:
    monkeypatch.setenv("DEPLOY_ALLOW_EMPTY", "HOOKJUDGE_AI_MODEL,HOOKJUDGE_AI_BASE_URL,HOOKPROBE_MODEL")
    root = _write(tmp_path, SECRETS_ONLY)
    assert preflight.main(["x", str(root)]) == 0, "an exception is a decision written where the deploy reads it"


def test_a_vendor_default_is_only_a_problem_when_the_env_is_silent(tmp_path, capsys) -> None:
    """The default itself is not the defect — it is what makes the quickstart
    runnable. Inheriting it on a production host is."""
    root = _write(tmp_path, FULL_ENV)
    assert preflight.main(["x", str(root)]) == 0
    assert "every brain pointed by .env" in capsys.readouterr().out
