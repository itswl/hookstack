"""scripts/doctor.py names the three locks before a person tries to press one.

Pilot zero's approve button was never drawn, its console was unreachable, and
its allowlist was empty. None of those is a service bug; all three are visible
in the deployment's own files, which is what the doctor reads.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("doctor", Path(__file__).resolve().parents[2] / "scripts" / "doctor.py")
doctor = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules["doctor"] = doctor
_spec.loader.exec_module(doctor)


def _env_file(tmp_path: Path, body: str) -> Path:
    path = tmp_path / ".env"
    path.write_text(body, encoding="utf-8")
    return path


CONFIG = """
sources:
  - name: grafana
    secret: ${GRAFANA}
card_actions:
  approve:
    forward_to: to-work
  useful:
    forward_to: to-judge
  useless:
    forward_to: to-judge
"""


def _levels(checks):
    return {check.name: check.level for check in checks}


def test_a_thin_env_names_the_three_pilot_zero_locks(tmp_path: Path) -> None:
    env_file = _env_file(tmp_path, "")
    levels = _levels(doctor.file_checks({}, "", env_file))

    assert levels["remediation allowlist"] == doctor.MUST
    assert levels["approve button"] == doctor.MUST
    assert levels["console link"] == doctor.SHOULD
    assert levels["card-action secret"] == doctor.MUST


def test_a_complete_env_has_nothing_to_fix(tmp_path: Path) -> None:
    allow = tmp_path / "allow.txt"
    allow.write_text("kubectl rollout restart .*\n", encoding="utf-8")
    env = {
        "HOOKRELAY_READ_TOKEN": "read",
        "WORK_ADMIN_TOKEN": "admin",
        "HOOKPROBE_TOKEN": "probe",
        "HOOKPROBE_ACTION_SECRET": "action",
        "ANTHROPIC_API_KEY": "key",
        "HOOKPROBE_PUBLIC_URL": "https://node.example/ui",
        "HOOKPROBE_REMEDIATION_ALLOWLIST": str(allow),
        "BACKUP_OFFSHOST_DEST": "rsync://backup/hookstack",
    }
    checks = doctor.file_checks(env, CONFIG, tmp_path / ".env")
    levels = _levels(checks)

    assert doctor.MUST not in levels.values(), [c for c in checks if c.level == doctor.MUST]
    assert levels["approve button"] == doctor.OK and levels["remediation allowlist"] == doctor.OK
    assert levels["off-host backup"] == doctor.OK


def test_an_empty_allowlist_file_is_not_a_ready_allowlist(tmp_path: Path) -> None:
    allow = tmp_path / "allow.txt"
    allow.write_text("# nothing yet\n\n", encoding="utf-8")
    env = {
        "HOOKRELAY_READ_TOKEN": "r",
        "WORK_ADMIN_TOKEN": "a",
        "HOOKPROBE_TOKEN": "p",
        "HOOKPROBE_ACTION_SECRET": "s",
        "ANTHROPIC_API_KEY": "k",
        "HOOKPROBE_PUBLIC_URL": "https://node.example/ui",
        "HOOKPROBE_REMEDIATION_ALLOWLIST": str(allow),
        "BACKUP_OFFSHOST_DEST": "rsync://backup/hookstack",
    }
    levels = _levels(doctor.file_checks(env, CONFIG, tmp_path / ".env"))
    assert levels["remediation allowlist"] == doctor.MUST


def test_an_unknown_config_still_checks_the_environment(tmp_path: Path) -> None:
    env_file = _env_file(tmp_path, "HOOKPROBE_TOKEN=p\n")
    checks = doctor.file_checks({"HOOKPROBE_TOKEN": "p"}, "", env_file)
    assert any(check.name == "approve button" and check.level == doctor.MUST for check in checks)
