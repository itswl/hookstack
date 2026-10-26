"""The demo compose signs card presses with one secret and verifies with it too.

CI's stack smoke failed on the approve step because hookrelay shipped
`HOOKRELAY_ACTION_SECRET: demo-card-secret` while hookprobe got no
`HOOKPROBE_ACTION_SECRET` at all: the pipe drew a button and the investigator
answered 503. A service that looks healthy while its buttons cannot land is the
shape this test exists to catch, without needing Docker.

The quickstart compose carries the same pair since 0.8.0: pinned to images whose
investigator refuses an unsigned approve, it would have drawn the button and
stopped the demo at the press, and nothing here read that file.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.yml"
QUICKSTART = Path(__file__).resolve().parents[2] / "docker-compose.quickstart.yml"
STACK_CONFIG = Path(__file__).resolve().parents[2] / "hookrelay" / "examples" / "stack.yaml"


def _default(text: str, name: str) -> str:
    match = re.search(rf"(?m)^\s+{name}:\s*(\$\{{{name}:-([^}}]+)\}}|\S+)\s*$", text)
    assert match, f"{name} is not set in docker-compose.yml"
    return match.group(2) or match.group(1)


def test_the_demo_signs_and_verifies_card_actions_with_the_same_secret() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    signed = _default(text, "HOOKRELAY_ACTION_SECRET")
    verified = _default(text, "HOOKPROBE_ACTION_SECRET")
    assert signed == verified, (
        "the demo pipe signs with one action secret and the investigator verifies with another; "
        "approve will be refused even though the button is drawn"
    )


def test_the_pipe_signs_the_forwarded_press_with_the_door_it_verifies_with() -> None:
    text = STACK_CONFIG.read_text(encoding="utf-8")
    block = text.split("name: to-probe-action", 1)[1].split("- name:", 1)[0]
    assert "secret: ${HOOKPROBE_ACTION_SECRET}" in block, (
        "to-probe-action is unsigned while the action door requires a signature; every approve press is a 401"
    )


def _resolved(value: object) -> str:
    """A compose value as a run from an empty directory resolves it: ${VAR:-default} is the default."""
    text = str(value or "")
    match = re.fullmatch(r"\$\{(\w+):-([^}]*)\}", text)
    return match.group(2) if match else text


def _environment(compose: Path, service: str) -> dict[str, str]:
    env = yaml.safe_load(compose.read_text(encoding="utf-8"))["services"][service].get("environment") or {}
    if isinstance(env, list):
        env = dict(item.split("=", 1) for item in env)
    return {str(key): _resolved(value) for key, value in env.items()}


@pytest.mark.parametrize("compose", [COMPOSE, QUICKSTART], ids=lambda path: path.name)
def test_the_pipe_signs_the_forwarded_press_with_the_value_the_investigator_verifies(compose: Path) -> None:
    signed = _environment(compose, "hookrelay").get("HOOKPROBE_ACTION_SECRET", "")
    verified = _environment(compose, "hookprobe").get("HOOKPROBE_ACTION_SECRET", "")
    assert signed and signed == verified, (
        f"{compose.name}: the pipe forwards approve signed with {signed!r} and the investigator verifies with "
        f"{verified!r}; an unsigned or mismatched press is refused while the button still renders"
    )
