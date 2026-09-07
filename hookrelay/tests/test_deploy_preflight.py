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
"""


def _write(tmp_path: Path, env: str) -> Path:
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy" / "shadow.yaml").write_text(PIPE)
    (tmp_path / "deploy" / "docker-compose.shadow.yml").write_text(COMPOSE)
    (tmp_path / "hookprobe" / "deploy").mkdir(parents=True)
    (tmp_path / "hookprobe" / "deploy" / "docker-compose.prod.yml").write_text("services: {}\n")
    (tmp_path / ".env").write_text(env)
    return tmp_path


def test_a_complete_env_passes(tmp_path, capsys) -> None:
    root = _write(
        tmp_path, "WW_RELAY_SECRET=a\nSHADOW_RETURN_SECRET=b\nHOOKPROBE_RETURN_SECRET='c'\nSHADOW_READ_TOKEN=d\n"
    )
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
    root = _write(
        tmp_path, "WW_RELAY_SECRET=a\nSHADOW_RETURN_SECRET=b\nHOOKPROBE_RETURN_SECRET=c\nSHADOW_READ_TOKEN=d\n"
    )
    (root / "deploy" / "shadow.yaml").write_text(PIPE.replace("${HOOKPROBE_RETURN_SECRET}", '""'))
    assert preflight.main(["x", str(root)]) == 1
    assert "literal empty string" in capsys.readouterr().err


def test_an_exception_is_recorded_where_the_deploy_reads_it(tmp_path, monkeypatch) -> None:
    root = _write(tmp_path, "WW_RELAY_SECRET=a\nSHADOW_RETURN_SECRET=b\nHOOKPROBE_RETURN_SECRET=c\n")
    assert preflight.main(["x", str(root)]) == 1, "SHADOW_READ_TOKEN is missing"
    monkeypatch.setenv("DEPLOY_ALLOW_EMPTY", "SHADOW_READ_TOKEN")
    assert preflight.main(["x", str(root)]) == 0
