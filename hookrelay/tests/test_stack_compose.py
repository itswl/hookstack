"""The demo compose signs card presses with one secret and verifies with it too.

CI's stack smoke failed on the approve step because hookrelay shipped
`HOOKRELAY_ACTION_SECRET: demo-card-secret` while hookprobe got no
`HOOKPROBE_ACTION_SECRET` at all: the pipe drew a button and the investigator
answered 503. A service that looks healthy while its buttons cannot land is the
shape this test exists to catch, without needing Docker.
"""

from __future__ import annotations

import re
from pathlib import Path

COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.yml"
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
